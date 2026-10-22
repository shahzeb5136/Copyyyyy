# Dashboard Builder

Upload a CSV or Excel file, let an LLM work out what the columns mean, confirm it, get a dashboard, change
it in plain English, save it as a file, and rebuild it any time with a new version of the data.

* **Front end:** React + TypeScript (Vite), one page, Plotly charts.
* **Back end:** Python / FastAPI / pandas / Plotly. Deterministic engine, LLM only for inference.
* **LLM:** any provider behind one wrapper; primary target is Claude on Microsoft Foundry.
* **Deploys as:** one Docker image on Azure Container Apps, Entra ID sign-in, Blob Storage for uploads.

All sample data is synthetic (a fictional company).

## Quick start (local, no LLM key needed)

```bash
# back end
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                                   # optional: add your LLM key, or leave LLM_PROVIDER=mock
uvicorn app.main:app --reload --port 8000

# front end (second terminal)
cd frontend
npm ci
npm run dev                                            # http://localhost:5173 (proxies /api to :8000)
```

Or run both with `scripts/dev.sh` (macOS/Linux) / `scripts\dev.ps1` (Windows).
Without a key the app runs in **mock** mode: column meanings are guessed heuristically and a default
dashboard is built, so the whole UI works offline. Add a key to turn on the AI steps.

To serve the built front end from the API (what the container does):

```bash
cd frontend && npm run build          # writes frontend/dist, which the API serves at /
cd ../backend && uvicorn app.main:app --port 8000     # http://localhost:8000
```

## Tests and checks

```bash
cd backend && ruff check . && python -m pytest -q      # ~70 tests, no network
cd frontend && npm run lint && npm run build           # type-check + lint + bundle
```

When the API changes, regenerate the front-end types (CI checks they are committed):

```bash
cd backend && python scripts/export_openapi.py ../frontend/src/api/openapi.json
cd ../frontend && npm run gen:types
```

## Docker

```bash
docker build -t dashboard-builder .
docker run --rm -p 8000:8000 -e LLM_PROVIDER=foundry -e LLM_API_KEY=... \
  -e LLM_BASE_URL=https://<resource>.services.ai.azure.com/anthropic/ -e LLM_MODEL=claude-opus-5-5 dashboard-builder
```

## Documentation

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The architecture, decisions taken, module layout, API surface |
| [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) | Step-by-step: Azure DevOps repo, pipeline, Azure resources, Entra ID, LLM key, operations - with a checklist for the DevOps engineer |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | The app screen by screen, what the AI sees, tips |
| [docs/SPEC_FORMAT.md](docs/SPEC_FORMAT.md) | The dashboard file format and the transformation language |
| [infra/README.md](infra/README.md) | What the Bicep template creates |

## Repository layout

```
backend/     FastAPI app (app/), tests (tests/), scripts/
frontend/    React app (src/), generated API types (src/api/)
samples/     synthetic CSV/XLSX files offered on the home page
infra/       Bicep template + parameters
docs/        guides
Dockerfile, azure-pipelines.yml, .github/workflows/ci.yml
```

## License

MIT - see [LICENSE](LICENSE).
