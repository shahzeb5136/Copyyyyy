# Architecture: LLM-assisted Excel/CSV dashboard app

This document fixes the architecture. The functional behaviour (screens, inference logic, prompts) is
described in `docs/USER_GUIDE.md` and `docs/SPEC_FORMAT.md`; deployment steps are in `docs/DEPLOYMENT_GUIDE.md`.

## 1. What the system is

A single web app, reached by one link, where a user:

* **Creates a new dashboard.** Uploads a CSV/Excel file. The app infers what each column means (the
  *semantic model*), first deterministically and then with the LLM. The user confirms or edits the model
  (table + chat). The LLM designs a dashboard spec; the engine runs it and renders the charts. The user
  changes the dashboard in plain English (add/remove charts, add written summaries), then downloads it.
* **Recreates an old dashboard.** Uploads a data file plus the saved dashboard spec (JSON). The engine replays
  the spec against the new file. No LLM call is needed unless the spec no longer fits the data.
* **Asks questions.** A small "Ask a question" tab answers ad-hoc questions and lets the user pin the answer
  to the dashboard.

The dashboard spec JSON is the persistence mechanism. There is no database of users or dashboards in v1:
the user keeps their own spec files, which keeps access management minimal.

## 2. Decided stack

| Layer | Choice |
|---|---|
| Front end | React 19 + TypeScript, built with Vite |
| Charts | **Plotly.js** (decision taken, see §13). Figures are built server-side from the spec by Python/Plotly and rendered by `plotly.js-dist-min` in the browser. The front end never hand-codes a chart. |
| Back end | Python 3.11+, FastAPI, Pydantic v2, pandas (openpyxl for `.xlsx`), Plotly |
| LLM | External LLM via API key through one wrapper module (`backend/app/llm_client.py`). Primary assumption: **Claude on Microsoft Foundry** (`LLM_PROVIDER=foundry`); also supports Azure OpenAI / OpenAI-family deployments on Foundry (`azure_openai`), the first-party Anthropic and OpenAI APIs, and `mock` (no LLM). |
| Packaging | One Docker image containing the built front end and the API (`Dockerfile`) |
| Hosting | Azure Container Apps (App Service for Containers would work identically) |
| Image registry | Azure Container Registry |
| Source control + CI/CD | Azure DevOps Repos + Azure Pipelines (`azure-pipelines.yml`); a GitHub Actions workflow is kept for the GitHub mirror |
| Auth | Entra ID through the platform's built-in authentication (Easy Auth). No auth code in the app; `/api/me` reads the injected headers. |
| Storage | Azure Blob Storage for uploads, profiles, specs and job state (`STORAGE_MODE=blob`); a local directory for development / single replica (`STORAGE_MODE=local`) |
| Secrets | Container App secrets (swappable for Key Vault references), exposed as env vars |
| Infra as code | `infra/main.bicep` |

Not using Streamlit.

## 3. Runtime topology

```
User browser ──(HTTPS, Entra ID login via Easy Auth)──▶ Azure Container App (one URL)
                                                         ┌──────────────────────────────────┐
                                                         │ FastAPI process (uvicorn)        │
                                                         │  • GET /        → React build    │
                                                         │  • /api/*       → JSON API       │
                                                         │  • background job threads        │
                                                         └──────────┬───────────────────────┘
                                                                    │
                                           ┌────────────────────────┴─────────────────┐
                                           ▼                                          ▼
                                 LLM API (Microsoft Foundry)               Azure Blob Storage
                                 via llm_client.py only              uploads / profiles / specs / jobs

Azure DevOps Repo ─▶ Azure Pipeline ─▶ test ─▶ docker build ─▶ ACR ─▶ az containerapp update
```

* **Single origin.** FastAPI serves the static React build at `/` (SPA fallback to `index.html`) and the API
  under `/api`. No CORS, one URL. In development the Vite dev server proxies `/api` to FastAPI.
* **The front end is thin.** Upload, review table, chat panels, spec download/upload, rendering of Plotly
  figure JSON. All data processing and all LLM calls happen in the back end; the browser never sees the key.
* **Slow steps are jobs.** The LLM steps (`infer`, `refine-model`, `build`, `refine`, `narratives`, `ask`)
  return a `job_id` immediately; the front end polls `GET /api/jobs/{id}`. Job state is persisted through the
  storage layer so any replica can answer the poll. Fast deterministic steps (`upload`, `render`, `replay`)
  are synchronous.

## 4. The dashboard spec: the central contract

Defined once as Pydantic models in `backend/app/spec/models.py` (`spec_version` 1.0).

* The back end exports its OpenAPI document (`backend/scripts/export_openapi.py` →
  `frontend/src/api/openapi.json`) and the front end generates TypeScript types from it
  (`npm run gen:types` → `src/api/types.gen.ts`). The pipeline fails if the generated files are stale, so the
  two sides cannot drift.
* `spec_version` is checked explicitly on load (`spec/migrations.py`); unknown versions are rejected with a
  clear error, and future migrations are registered there.
* Contents: `metadata`, `source_fingerprint` (expected columns + types, row count), `semantic_model`
  (meaning, role, type, unit, default aggregation per column, data shape, business notes),
  `global_filters`, `widgets` (each with ordered declarative `steps`, a `chart` definition or a `text`
  block, and a grid `layout`).
* The spec contains no raw data and can be e-mailed around. `docs/SPEC_FORMAT.md` documents it.

**Safety rule (non-negotiable):** the LLM produces spec content, never executable code. Transformations are a
whitelisted set of operations (`engine/transforms.py`) implemented in pandas. Derived columns use a tiny
expression language with its own parser (`engine/expressions.py`); there is no `eval`/`exec` anywhere.
Every LLM response is parsed, validated against the Pydantic models, and executed against the real data
before use, with bounded retries that feed the errors back to the model.

## 5. Back-end structure

```
backend/
  app/
    main.py              # FastAPI app, /api routers, static front end + SPA fallback, error handlers
    settings.py          # pydantic-settings, env vars
    deps.py              # app-wide singletons (storage, jobs, LLM, dataset service)
    api/                 # thin route handlers: system.py, datasets.py, dashboards.py (+ schemas.py)
    services.py          # upload -> parse -> profile -> persist; DataFrame cache
    spec/                # Pydantic models for the spec + version migration
    engine/              # pure Python, deterministic: loader, profile, expressions, transforms, pipeline,
                         # charts (Plotly figure JSON), dashboard (spec -> widget results), compat
    inference/           # LLM-dependent layer: prompts, strict schemas, structured calls with retry,
                         # semantic model, dashboard design/refine, narratives, ask; heuristics + fallback
    llm_client.py        # the ONLY place that talks to the LLM provider
    storage.py           # local directory | Azure Blob, one interface
    jobs.py              # background jobs persisted through storage
    auth.py              # Easy Auth headers -> principal
  tests/                 # pytest (no network, no key): engine, spec, replay determinism, API flows
  scripts/               # export_openapi.py, make_sample_xlsx.py
```

`engine/` never calls the LLM: file + spec → dashboard data, deterministically. That is what makes
"recreate old dashboard" work without the LLM. `inference/` is the only LLM-dependent layer and turns
(column profile + sample rows + user comments) into spec objects.

Data sent to the LLM is limited to column names, inferred types, summary statistics (percentiles, nulls,
distinct counts), the top-N distinct values of text columns (`LLM_MAX_DISTINCT_VALUES`, default 100) and a
few sample rows (`LLM_SAMPLE_ROWS`, default 5). Narratives and answers receive the *computed* chart data
(at most a few dozen rows), never the file.

### API surface

| Method | Path | Purpose | Mode |
|---|---|---|---|
| POST | `/api/datasets` | Upload CSV/XLSX → `dataset_id`, sheet list, column profile, preview | sync |
| GET | `/api/datasets/{id}` | Dataset info | sync |
| POST | `/api/datasets/{id}/sheet` | Switch to another sheet of the workbook | sync |
| POST | `/api/datasets/{id}/infer` | LLM proposes the semantic model | job |
| POST | `/api/datasets/{id}/refine-model` | Chat loop on the semantic model | job |
| POST | `/api/datasets/{id}/build` | Semantic model in → spec + rendered dashboard out | job |
| POST | `/api/datasets/{id}/render` | Spec in → rendered dashboard (no LLM; used after client edits) | sync |
| POST | `/api/datasets/{id}/refine` | Chat loop on the dashboard spec | job |
| POST | `/api/datasets/{id}/narratives` | (Re)write generated text widgets | job |
| POST | `/api/datasets/{id}/ask` | Ad-hoc question → plan, result, answer, chart | job |
| POST | `/api/replay` | File + saved spec in → compatibility report + dashboard (no LLM) | sync |
| GET | `/api/jobs/{id}` | Job status / result | sync |
| GET | `/api/health`, `/api/me`, `/api/spec-schema`, `/api/samples` | Probes, identity, schema, sample files | sync |

Interactive docs: `/api/docs`.

## 6. LLM integration

* `llm_client.py` wraps the provider. The rest of the code calls `complete_json(system, user, schema)` /
  `complete_text(...)` and never imports a provider SDK.
* Provider, key, endpoint and model come from env vars: `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_BASE_URL`,
  `LLM_MODEL` (plus `LLM_FOUNDRY_RESOURCE` / `LLM_API_VERSION` where relevant). Nothing in code or the repo.
* Structured output is requested from the provider where supported (Anthropic `output_config` JSON schema,
  OpenAI strict JSON schema with an automatic fallback to JSON mode); the result is always validated with
  Pydantic regardless, and re-asked with the validation/execution errors up to 3 times.
* Timeouts, SDK retries and an outer retry with backoff on rate limits / 5xx / timeouts are built in. Token
  usage and latency are logged per call; prompts and responses are not logged unless `LLM_LOG_PROMPTS=true`.
* Long-running calls use the job pattern (§3), so platform ingress timeouts are not an issue.

## 7. State and scaling

* Preferred: `STORAGE_MODE=blob`. Uploaded files, profiles, specs and job records live in Blob Storage keyed
  by `dataset_id`/`job_id`; the API stays stateless and Container Apps can run several replicas. The app
  deletes objects older than `RETENTION_HOURS` (default 72) and the Bicep adds a storage lifecycle rule as a
  second safety net.
* Pilot alternative: `STORAGE_MODE=local` with one replica. `storage.py` hides the choice.
* Blob and ACR are accessed with a user-assigned managed identity (`AZURE_CLIENT_ID`), not connection strings.
* Upload size (`MAX_UPLOAD_MB`), row count (`MAX_ROWS`), column count and chart row limits are enforced;
  unsupported file types are rejected before parsing.

## 8. Front-end structure

```
frontend/src/
  api/          # client.ts (typed fetch + job polling), openapi.json + types.gen.ts (generated), types.ts
  pages/        # HomePage (create | rebuild), DataPage, ModelPage (review table + chat), DashboardPage, AskPanel
  components/   # FileDrop, Stepper, ChatPanel, WidgetCard, PlotlyChart (lazy-loaded), DataTable, Markdown, Banner
  App.tsx       # the one-page state machine: home → data → model → dashboard
```

## 9. Authentication

Built-in authentication on the Container App with Entra ID, sign-in required for all routes (configured in
`infra/main.bicep`). The app reads `X-MS-CLIENT-PRINCIPAL-NAME` / `X-MS-CLIENT-PRINCIPAL` for `/api/me` and
for the `created_by` field in the spec; `REQUIRE_EASY_AUTH=true` makes the API refuse requests without those
headers (defence in depth). No user tables or roles in v1.

## 10. Repository, Docker and pipeline

```
/frontend                React app
/backend                 FastAPI app + tests
/samples                 synthetic sample files (CSV + XLSX)
/infra                   Bicep
/docs                    deployment guide, user guide, spec format
/Dockerfile              multi-stage: node build → python slim runtime, non-root user, health check
/azure-pipelines.yml     Test → Image (ACR) → Deploy (az containerapp update), PR validation on pull requests
/.github/workflows       CI for the GitHub mirror
/ARCHITECTURE.md         this file
```

Configuration (env vars set on the Container App, never committed): `LLM_PROVIDER`, `LLM_API_KEY`,
`LLM_BASE_URL`, `LLM_MODEL`, `STORAGE_MODE`, `BLOB_ACCOUNT_URL`, `BLOB_CONTAINER`, `MAX_UPLOAD_MB`,
`LLM_SAMPLE_ROWS`, `LLM_MAX_DISTINCT_VALUES`, `RETENTION_HOURS`, `REQUIRE_EASY_AUTH`, `LOG_LEVEL`.
See `backend/.env.example` for the full list.

## 11. Testing

`backend/tests` (run with `pytest`, no network or key needed):

* **Spec replay is deterministic** - same file + spec twice gives byte-identical output (`test_determinism.py`),
  and the API test replays a downloaded spec against the same file and compares every figure.
* **Spec validation** - malformed steps, unknown versions and incompatible files produce clear errors
  ("Columns used by the dashboard are missing from the new file: …").
* **LLM output validation** - a scripted fake provider returns bad/partial JSON; the system retries with the
  errors, drops widgets it cannot fix, and falls back to the heuristic dashboard when the provider fails.
* **File parsing edge cases** - multi-sheet Excel, title rows above the header, data in the first row, blank
  or duplicate headers, cp1252/semicolon CSVs, day-first dates, mixed-type columns, row limits.

The front end is type-checked, linted and built in CI; the generated API types must match the back end.

## 12. Explicitly out of scope for v1

User accounts/roles/per-user dashboard libraries, a relational database, executing LLM-generated code,
real-time collaboration, live data sources (file upload only), dark mode for charts.

## 13. Decisions taken on the open points

| Open decision | Decision | Why |
|---|---|---|
| ECharts vs Plotly.js | **Plotly.js**, with figures built server-side in Python | The chart logic (13 chart types, formatting, status colours, fallbacks) lives in Python next to the data and the tests, which is where this team is strongest; the front end only needs one `PlotlyChart` component. Plotly is lazy-loaded (1.4 MB gzipped, cached). |
| Blob-backed state vs single replica | **Blob** in production (Bicep sets it up), local directory for development | Keeps the API stateless; switching is a config change. |
| Sync vs job/polling for LLM steps | **Jobs with polling** for every LLM step from day one | LLM latency for dashboard design is tens of seconds; polling also survives multi-replica deployments. |
| Fingerprint strictness on replay | **Build what can be built, warn about the rest** | Missing/retyped columns are reported per widget and in a banner; the chat can repair the spec ("column X was renamed to Y"). Replay is refused only when no chart at all can be built. |
