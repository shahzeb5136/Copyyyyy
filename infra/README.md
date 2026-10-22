# Infrastructure (Bicep)

`main.bicep` creates everything the app needs in one resource group:

| Resource | Purpose |
|---|---|
| Log Analytics workspace | Container logs (`az containerapp logs show`) |
| Container Apps environment + Container App | Runs the image, external HTTPS ingress on port 8000, 1-3 replicas |
| Azure Container Registry (Basic) | Stores the image built by the pipeline |
| Storage account + `dashboard-builder` container | Uploaded files, profiles, specs and job state (`STORAGE_MODE=blob`) |
| User-assigned managed identity | `AcrPull` on the registry, `Storage Blob Data Contributor` on the storage account |
| Auth config on the Container App | Entra ID built-in authentication (Easy Auth), sign-in required for every request |

Secrets (`LLM_API_KEY`, the Entra client secret) are Container App secrets. Swap them for Key Vault
references later without touching the application (`secretRef` works the same way).

Deploy:

```bash
az group create -n rg-dashboard-builder -l uaenorth
az deployment group create -g rg-dashboard-builder -f infra/main.bicep -p infra/main.bicepparam \
  -p llmApiKey="$LLM_KEY" -p entraClientSecret="$ENTRA_SECRET"
```

The deployment prints `redirectUri`; add it to the Entra app registration (Authentication -> Web -> Redirect URIs).
See `docs/DEPLOYMENT_GUIDE.md` for the full step-by-step.
