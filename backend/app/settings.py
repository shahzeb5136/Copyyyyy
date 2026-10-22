"""Application settings, read from environment variables (and an optional .env file).

Every setting below can be provided as an environment variable with the same name in
upper case, e.g. ``LLM_API_KEY``. Nothing secret lives in the repository.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- general -----------------------------------------------------------
    app_name: str = "Dashboard Builder"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    # --- LLM -----------------------------------------------------------------
    # Providers: foundry (Claude on Microsoft Foundry) | azure_openai (OpenAI models on Foundry/Azure OpenAI)
    #            | anthropic (first-party) | openai (OpenAI or any OpenAI-compatible gateway) | mock (no LLM).
    # auto -> guesses from the other settings; set it explicitly in production.
    llm_provider: str = "auto"
    llm_api_key: str = ""
    llm_base_url: str = ""  # foundry: https://<resource>.services.ai.azure.com/anthropic/  azure_openai: resource endpoint
    llm_model: str = ""  # model id, or the deployment name on Azure/Foundry
    llm_foundry_resource: str = ""  # foundry: resource name (alternative to LLM_BASE_URL)
    llm_api_version: str = "2024-10-21"  # Azure OpenAI only
    llm_timeout_seconds: float = 180.0
    llm_max_retries: int = 2
    llm_effort: str = ""  # Anthropic only: low | medium | high | xhigh | max ("" = provider default)
    llm_anthropic_fallbacks: bool = False  # opt in to Anthropic server-side refusal fallbacks (beta)
    llm_sample_rows: int = 5  # sample rows sent to the LLM per request
    llm_max_distinct_values: int = 100  # top-N distinct text values sent to the LLM per column
    llm_log_prompts: bool = False  # never on by default: prompts contain user data samples

    # --- storage -------------------------------------------------------------
    storage_mode: str = "local"  # local | blob
    storage_local_dir: str = str(REPO_DIR / ".data")
    blob_account_url: str = ""  # https://<account>.blob.core.windows.net (managed identity)
    blob_container: str = "dashboard-builder"
    blob_connection_string: str = ""  # dev fallback only; prefer managed identity
    retention_hours: int = 72  # uploaded files and jobs older than this are deleted

    # --- limits --------------------------------------------------------------
    max_upload_mb: int = 50
    max_rows: int = 1_000_000
    max_columns: int = 300
    max_chart_rows: int = 5_000  # rows a single chart dataset may contain
    max_table_rows: int = 500  # rows returned for table widgets / ask results
    job_workers: int = 4

    # --- web -----------------------------------------------------------------
    static_dir: str = ""  # directory with the built front end; auto-detected when empty
    samples_dir: str = ""  # directory with sample files offered on the home page; defaults to <repo>/samples
    require_easy_auth: bool = False  # reject /api requests without Easy Auth headers

    # ------------------------------------------------------------------------
    @property
    def effective_llm_provider(self) -> str:
        p = (self.llm_provider or "auto").strip().lower().replace("-", "_")
        if p in {"microsoft_foundry", "azure_foundry", "azure_ai_foundry"}:
            p = "foundry"
        if p != "auto":
            return p
        if not self.llm_api_key:
            return "mock"
        url = self.llm_base_url.lower()
        if self.llm_foundry_resource:
            return "foundry"
        if "openai.azure.com" in url or "cognitiveservices.azure.com" in url:
            return "azure_openai"
        if "services.ai.azure.com" in url:
            return "foundry" if (not self.llm_model or self.llm_model.lower().startswith("claude")) else "azure_openai"
        if self.llm_api_key.startswith("sk-ant-"):
            return "anthropic"
        if self.llm_api_key.startswith("sk-"):
            return "openai"
        return "anthropic"

    @property
    def effective_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return {"anthropic": "claude-opus-5-5", "foundry": "claude-opus-5-5", "openai": "gpt-5.5",
                "azure_openai": "gpt-5.5"}.get(self.effective_llm_provider, "mock")

    @property
    def llm_enabled(self) -> bool:
        return self.effective_llm_provider != "mock"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def resolved_samples_dir(self) -> Path:
        return Path(self.samples_dir) if self.samples_dir else REPO_DIR / "samples"

    def resolved_static_dir(self) -> Path | None:
        candidates = [Path(self.static_dir)] if self.static_dir else []
        candidates += [BACKEND_DIR / "static", REPO_DIR / "frontend" / "dist"]
        for c in candidates:
            if c and (c / "index.html").exists():
                return c
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
