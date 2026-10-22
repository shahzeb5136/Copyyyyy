# Deployment guide

A step-by-step path from "I have the zip on my work PC" to "the app runs on Azure Container Apps behind
Entra ID sign-in, deployed by Azure Pipelines". Each step says who normally does it (**you** = the product
owner / developer, **DevOps** = the DevOps or platform engineer) and what to hand over.

> Everything below was written against the Azure CLI and portal as of late 2026. Names in `<angle brackets>`
> are yours to choose. Nothing secret is ever committed to the repository.

---

## 0. What you need before you start

| Item | Who | Notes |
|---|---|---|
| Azure subscription + a resource group in **UAE North** (`uaenorth`) | DevOps | e.g. `rg-dashboard-builder-prod` (and optionally `-dev`) |
| Azure DevOps project with Repos and Pipelines enabled | DevOps | Parallel-job quota for hosted agents must be available |
| Permission to create an **Entra ID app registration** | DevOps / identity team | Needed for sign-in (Easy Auth) |
| **LLM API key** on Microsoft Foundry | You | A Foundry resource with a Claude deployment (or an OpenAI-family deployment). You need the endpoint URL, the key and the model/deployment name. |
| On your PC: Python 3.11+, Node 22+, Git, Docker Desktop (optional, for local image builds) | You | |

---

## 1. Put the code into Azure DevOps (you, 15 min)

1. Unzip `dashboard-builder.zip` into a folder, e.g. `C:\src\dashboard-builder`.
2. In Azure DevOps create a new repository (Repos → New repository → name `dashboard-builder`, no README).
3. Push the code:
   ```powershell
   cd C:\src\dashboard-builder
   git init
   git add .
   git commit -m "Dashboard Builder v1"
   git branch -M main
   git remote add origin https://dev.azure.com/<org>/<project>/_git/dashboard-builder
   git push -u origin main
   ```
   (If the zip came from the GitHub repo with history, `git remote add` + `git push` is enough.)
4. Recommended branch policy on `main` (Repos → Branches → `main` → Branch policies): require a pull request,
   require the build validation pipeline (added in step 6) to pass.

---

## 2. Run it on your PC first (you, 20 min)

```powershell
cd C:\src\dashboard-builder\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env        # edit: put LLM_PROVIDER/LLM_API_KEY/LLM_BASE_URL/LLM_MODEL in, or keep LLM_PROVIDER=mock
python -m pytest -q           # all tests should pass, no key needed
uvicorn app.main:app --reload --port 8000
```

Second terminal:

```powershell
cd C:\src\dashboard-builder\frontend
npm ci
npm run dev                   # opens http://localhost:5173, /api is proxied to port 8000
```

Open http://localhost:5173, click a sample file, walk through the four steps. Without a key you get the
heuristic dashboard; with a key the AI steps run. `scripts\dev.ps1` starts both servers at once.

To see exactly what the container will serve: `npm run build` in `frontend`, then start only uvicorn and
open http://localhost:8000.

---

## 3. Configure the LLM (you, 10 min)

The app talks to the model through one module (`backend/app/llm_client.py`); the choice is pure configuration.

### Claude on Microsoft Foundry (primary assumption)

In the Foundry portal, deploy a Claude model in your resource (UAE North if available; otherwise the nearest
allowed region - the app does not care where the model lives). Copy from the deployment page:

| Setting | Value |
|---|---|
| `LLM_PROVIDER` | `foundry` |
| `LLM_API_KEY` | the Foundry key |
| `LLM_BASE_URL` | `https://<resource>.services.ai.azure.com/anthropic/` (the Anthropic-compatible endpoint of the resource). Alternatively set `LLM_FOUNDRY_RESOURCE=<resource>` instead of the URL. |
| `LLM_MODEL` | the model/deployment name, e.g. `claude-opus-5-5` |

### OpenAI-family model on Foundry / Azure OpenAI

| Setting | Value |
|---|---|
| `LLM_PROVIDER` | `azure_openai` |
| `LLM_API_KEY` | the key |
| `LLM_BASE_URL` | `https://<resource>.openai.azure.com/` |
| `LLM_MODEL` | the **deployment name** |
| `LLM_API_VERSION` | e.g. `2024-10-21` |

### Checking it works

Start the back end with the variables set and call `GET /api/health`: `llm.configured` must be `true` and
`llm.provider`/`llm.model` must show your values. Then upload a sample and run "Analyse columns": the reply
should be a real description, not "No AI model is configured".

If the company routes outbound traffic through a proxy or gateway, `LLM_BASE_URL` can point at the gateway
(for OpenAI-compatible gateways use `LLM_PROVIDER=openai`). Egress from the Container App to the LLM endpoint
must be allowed by the network team.

---

## 4. Create the Azure resources (DevOps, 30 min)

Option A - **Bicep** (recommended, repeatable). From the repo root, logged in with `az login`:

```bash
az group create -n rg-dashboard-builder-prod -l uaenorth
az deployment group create -g rg-dashboard-builder-prod -f infra/main.bicep \
  -p namePrefix=dashb location=uaenorth \
  -p entraClientId=<from step 5> -p entraClientSecret=<from step 5> \
  -p llmProvider=foundry -p llmBaseUrl=https://<resource>.services.ai.azure.com/anthropic/ \
  -p llmModel=claude-opus-5-5 -p llmApiKey=<key>
```

The template creates: Log Analytics, Container Apps environment, Container Registry, Storage account with a
`dashboard-builder` blob container and a 7-day lifecycle rule, a user-assigned managed identity with
`AcrPull` + `Storage Blob Data Contributor`, the Container App (placeholder image, 1-3 replicas, health
probes, secrets `llm-api-key` and `entra-client-secret`) and the Easy Auth configuration.
Outputs: `appUrl`, `acrLoginServer`, `redirectUri` (needed in step 5).

Option B - **portal / CLI by hand**: create the same resources; the essential settings are in
`infra/main.bicep` and `infra/README.md`. Key points: ingress target port **8000**, managed identity for ACR
pull and blob access (`AZURE_CLIENT_ID` env var = the identity's client id), env vars from the table in
section 8, `STORAGE_MODE=blob`.

> Tip: run the Bicep twice if the role assignments race the app creation on the very first run; the template
> is idempotent.

---

## 5. Entra ID sign-in (DevOps / identity team, 20 min)

1. Entra ID → App registrations → **New registration**: name `Dashboard Builder`, single tenant.
2. Authentication → Add a platform → **Web** → Redirect URI = the `redirectUri` output of step 4
   (`https://<app>.<region>.azurecontainerapps.io/.auth/login/aad/callback`). Enable **ID tokens**.
3. Certificates & secrets → New client secret → copy the value (this is `entraClientSecret`).
4. Expose an API → Application ID URI → set to `api://<client id>` (the Bicep allows this audience).
5. Re-run the Bicep deployment (or update the Container App's authentication blade) with the client id and
   secret. Easy Auth then requires sign-in for every request; the app reads the signed-in user from the
   headers (`GET /api/me`).
6. Optional: restrict who can sign in - Enterprise applications → Dashboard Builder → Properties →
   "Assignment required" = Yes, then assign users/groups.

Nothing in the code depends on the tenant; swapping the app registration later is a config change.

---

## 6. Azure Pipelines (DevOps, 30 min)

1. **Service connections** (Project settings → Service connections):
   * *Azure Resource Manager* (workload identity federation or service principal) scoped to the resource
     group → name it, e.g. `sc-azure-dashboard-builder`. It needs **Contributor** on the resource group.
   * *Docker Registry* → Azure Container Registry → pick the registry created in step 4 → name it, e.g.
     `sc-acr-dashboard-builder`.
2. **Variable group** (Pipelines → Library → + Variable group) named exactly `dashboard-builder`:

   | Variable | Value |
   |---|---|
   | `AZURE_SERVICE_CONNECTION` | `sc-azure-dashboard-builder` |
   | `ACR_SERVICE_CONNECTION` | `sc-acr-dashboard-builder` |
   | `ACR_LOGIN_SERVER` | `<acr name>.azurecr.io` (Bicep output `acrLoginServer`) |
   | `RESOURCE_GROUP` | `rg-dashboard-builder-prod` |
   | `CONTAINER_APP_NAME` | `dashb-app` (`<namePrefix>-app`) |

3. **Environment**: Pipelines → Environments → New → `production`. Add an *Approvals* check if deployments
   must be approved by a person.
4. **Pipeline**: Pipelines → New pipeline → Azure Repos Git → the repo → "Existing Azure Pipelines YAML
   file" → `/azure-pipelines.yml` → Run.

What the pipeline does: stage **Test** (back end lint + pytest with results published; front end type
generation check, lint, build) → stage **Image** (docker build + push to ACR, tags `latest` and
`<buildId>-<sha>`) → stage **Deploy** (`az containerapp update --image …` on `main` only). Pull requests run
only the Test stage.

5. Add the pipeline as **build validation** on the `main` branch policy (step 1.4).

First successful run → open the `appUrl` from step 4 → sign in → use the app.

---

## 7. Day-2 operations

| Task | How |
|---|---|
| Logs | `az containerapp logs show -n dashb-app -g rg-dashboard-builder-prod --follow` or Log Analytics (`ContainerAppConsoleLogs_CL`). Each LLM call logs provider, model, tokens and latency. Set `LOG_LEVEL=DEBUG` + `LLM_LOG_PROMPTS=true` only for debugging - prompts contain data samples. |
| Rotate the LLM key | `az containerapp secret set -n dashb-app -g <rg> --secrets llm-api-key=<new>` then `az containerapp revision restart` (or re-run the Bicep). |
| Change the model | Update `LLM_MODEL` (`az containerapp update --set-env-vars LLM_MODEL=…`). |
| Scale | `minReplicas`/`maxReplicas` in the Bicep (default 1-3, HTTP concurrency 40). Uploads are shared via Blob, so replicas are interchangeable. |
| Retention | `RETENTION_HOURS` (default 72) - the app deletes old uploads, profiles, specs and jobs; the storage lifecycle rule deletes anything older than 7 days regardless. |
| Larger files | `MAX_UPLOAD_MB` (default 50), `MAX_ROWS` (default 1,000,000); raise container memory (`2Gi` default) for very large files. |
| Health | `GET /api/health` (used by the liveness/readiness probes). |
| Interactive API docs | `https://<app>/api/docs` (behind sign-in). |
| Roll back | `az containerapp revision list` → `az containerapp update --image <previous tag>` (every build tag stays in ACR). |

---

## 8. Configuration reference (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `auto` | `foundry`, `azure_openai`, `anthropic`, `openai`, `mock`. Set it explicitly in production. |
| `LLM_API_KEY` | | The key. Empty + `auto` ⇒ mock mode. |
| `LLM_BASE_URL` | | Endpoint (see section 3). |
| `LLM_MODEL` | provider default | Model id or deployment name. |
| `LLM_FOUNDRY_RESOURCE` | | Foundry resource name (alternative to `LLM_BASE_URL`). |
| `LLM_API_VERSION` | `2024-10-21` | Azure OpenAI only. |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `180` / `2` | Per call. |
| `LLM_EFFORT` | | Anthropic/Foundry only: `low`…`max`. |
| `LLM_SAMPLE_ROWS` | `5` | Sample rows sent to the model. |
| `LLM_MAX_DISTINCT_VALUES` | `100` | Top distinct values per text column sent to the model. |
| `LLM_LOG_PROMPTS` | `false` | Log full prompts/responses (dev only). |
| `STORAGE_MODE` | `local` | `local` or `blob`. |
| `STORAGE_LOCAL_DIR` | `<repo>/.data` | Local mode directory (`/app/data` in the image). |
| `BLOB_ACCOUNT_URL` / `BLOB_CONTAINER` | | Blob mode (managed identity; `AZURE_CLIENT_ID` selects the identity). |
| `BLOB_CONNECTION_STRING` | | Dev-only alternative to managed identity. |
| `RETENTION_HOURS` | `72` | Auto-delete window. `0` disables. |
| `MAX_UPLOAD_MB` / `MAX_ROWS` / `MAX_COLUMNS` | `50` / `1000000` / `300` | Upload limits. |
| `MAX_CHART_ROWS` / `MAX_TABLE_ROWS` | `5000` / `500` | Result size limits. |
| `JOB_WORKERS` | `4` | Background job threads per replica. |
| `REQUIRE_EASY_AUTH` | `false` | Reject API calls without Easy Auth headers (set `true` in production). |
| `STATIC_DIR` / `SAMPLES_DIR` | auto | Built front end / sample files (set by the Dockerfile). |
| `LOG_LEVEL` | `INFO` | |

---

## 9. Hand-over checklist for the DevOps engineer

- [ ] Resource group in `uaenorth`; run `infra/main.bicep` (section 4) - send back `appUrl`, `acrLoginServer`, `redirectUri`.
- [ ] Entra app registration with the redirect URI; client id + secret into the Bicep/Container App (section 5).
- [ ] Outbound access from the Container App to the Foundry endpoint (and to `*.blob.core.windows.net`).
- [ ] Two service connections (ARM + ACR), the `dashboard-builder` variable group, the `production` environment (section 6).
- [ ] Create the pipeline from `/azure-pipelines.yml`; add it as build validation on `main`.
- [ ] Confirm `GET /api/health` shows `llm.configured: true` and `storage: blob` after the first deployment.
- [ ] Decide the retention window (`RETENTION_HOURS`) with the data owner; note that uploaded files are deleted automatically.

---

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Home page says "AI features are switched off" | `LLM_API_KEY` empty or `LLM_PROVIDER=mock`. Check the Container App secrets/env; `GET /api/health`. |
| "The LLM API key was rejected" | Wrong key or the key belongs to another resource/region. |
| "The configured model … was not found" | `LLM_MODEL` must be the deployment/model name shown in Foundry. |
| "Couldn't reach the AI provider" | Egress blocked, wrong `LLM_BASE_URL`, or proxy needed. |
| Jobs stay `queued`/`running` forever | Container restarted mid-job (check logs/memory); re-run the step. With several replicas make sure `STORAGE_MODE=blob`. |
| Upload rejected: "column names in the first row" | The sheet has a title row/blank rows above the header - the user must fix the file (by design). |
| 401 from `/api/*` | Easy Auth cookie expired (reload) or `REQUIRE_EASY_AUTH=true` while Easy Auth is not enabled on the app. |
| Image build fails on `npm ci` | `frontend/package-lock.json` must be committed and in sync with `package.json`. |
| Pipeline: "Generated API types are out of date" | Run the two regeneration commands from the README and commit the result. |
