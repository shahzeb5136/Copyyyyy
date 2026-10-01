"""Application settings.

Settings are read, in order of precedence, from:

1. ``.streamlit/secrets.toml`` (or the Secrets panel on Streamlit Community Cloud)
2. Environment variables
3. The defaults below

See ``.streamlit/secrets.toml.example`` for every option.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

ANTHROPIC_MODELS: dict[str, str] = {
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
    "claude-opus-5-5": "Claude Opus 5.5",
}
OPENAI_MODELS: dict[str, str] = {
    "gpt-5.4-mini": "GPT-5.4 mini",
    "gpt-5.5": "GPT-5.5",
}
PROVIDERS: dict[str, str] = {"anthropic": "Anthropic (Claude)", "openai": "OpenAI"}
DEFAULT_MODEL = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5.4-mini"}


def _secrets() -> dict[str, Any]:
    """Return st.secrets as a plain dict, or {} when no secrets file exists."""
    try:
        import streamlit as st

        return {k: (dict(v) if hasattr(v, "keys") else v) for k, v in st.secrets.items()}
    except Exception:  # no secrets.toml, or running outside Streamlit
        return {}


def _get(secrets: dict, section: str, key: str, env: str | None = None, default: Any = None) -> Any:
    value = secrets.get(section, {}).get(key) if isinstance(secrets.get(section), dict) else None
    if value in (None, "") and env:
        value = os.environ.get(env)
    return default if value in (None, "") else value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Branding
    app_title: str = "Ask Your Data"
    app_tagline: str = "Ask questions in plain English. AI writes the SQL, reads the results and picks the chart."
    organization: str = ""
    organization_url: str = ""
    repo_url: str = ""

    # LLM
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL["anthropic"]
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    allow_user_api_keys: bool = True
    allow_model_choice: bool = True
    effort: str = ""  # optional Anthropic output_config.effort ("low" | "medium" | "high")

    # Guardrails
    max_questions_per_session: int = 25  # 0 = unlimited
    max_result_rows: int = 5_000
    query_timeout_seconds: float = 15.0
    max_upload_rows: int = 500_000

    # Access control: "none" | "password" | "oidc" (Streamlit's native st.login)
    access_mode: str = "none"
    users: dict[str, str] = field(default_factory=dict)
    show_demo_credentials: bool = False
    oidc_provider: str = ""
    allowed_emails: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()

    def api_key_for(self, provider: str) -> str:
        if provider == "offline":
            return "offline"
        return self.anthropic_api_key if provider == "anthropic" else self.openai_api_key

    @property
    def offline(self) -> bool:
        return self.provider == "offline"

    @property
    def has_server_key(self) -> bool:
        return self.offline or bool(self.anthropic_api_key or self.openai_api_key)


def load_settings() -> Settings:
    s = _secrets()
    provider = str(_get(s, "llm", "provider", "LLM_PROVIDER", "anthropic")).lower()
    if provider not in PROVIDERS and provider != "offline":
        provider = "anthropic"

    # NB: the [auth] section is reserved by Streamlit for native OIDC login, so ours is [access].
    access = s.get("access", {}) if isinstance(s.get("access"), dict) else {}
    users = {str(k): str(v) for k, v in dict(access.get("users", {}) or {}).items()}
    # Single shared password via env vars, e.g. for Docker deployments.
    if not users and os.environ.get("APP_PASSWORD"):
        users = {os.environ.get("APP_USERNAME", "demo"): os.environ["APP_PASSWORD"]}
    default_mode = "password" if users else "none"
    access_mode = str(_get(s, "access", "mode", "ACCESS_MODE", default_mode)).lower()
    if access_mode not in {"none", "password", "oidc"} or (access_mode == "password" and not users):
        access_mode = "none"

    def _list(key: str) -> tuple[str, ...]:
        value = access.get(key, []) or []
        if isinstance(value, str):
            value = [v for v in value.split(",") if v.strip()]
        return tuple(str(v).strip().lower() for v in value)

    return Settings(
        app_title=str(_get(s, "app", "title", "APP_TITLE", Settings.app_title)),
        app_tagline=str(_get(s, "app", "tagline", "APP_TAGLINE", Settings.app_tagline)),
        organization=str(_get(s, "app", "organization", "APP_ORGANIZATION", "")),
        organization_url=str(_get(s, "app", "organization_url", "APP_ORGANIZATION_URL", "")),
        repo_url=str(_get(s, "app", "repo_url", "APP_REPO_URL", "")),
        provider=provider,
        model=str(_get(s, "llm", "model", "LLM_MODEL", DEFAULT_MODEL.get(provider, "offline-replay"))),
        anthropic_api_key=str(_get(s, "llm", "anthropic_api_key", "ANTHROPIC_API_KEY", "")),
        openai_api_key=str(_get(s, "llm", "openai_api_key", "OPENAI_API_KEY", "")),
        allow_user_api_keys=_as_bool(_get(s, "llm", "allow_user_api_keys", "ALLOW_USER_API_KEYS", True)),
        allow_model_choice=_as_bool(_get(s, "llm", "allow_model_choice", "ALLOW_MODEL_CHOICE", True)),
        effort=str(_get(s, "llm", "effort", "LLM_EFFORT", "")).lower(),
        max_questions_per_session=int(_get(s, "limits", "max_questions_per_session", "MAX_QUESTIONS_PER_SESSION", 25)),
        max_result_rows=int(_get(s, "limits", "max_result_rows", "MAX_RESULT_ROWS", 5_000)),
        query_timeout_seconds=float(_get(s, "limits", "query_timeout_seconds", "QUERY_TIMEOUT_SECONDS", 15)),
        max_upload_rows=int(_get(s, "limits", "max_upload_rows", "MAX_UPLOAD_ROWS", 500_000)),
        access_mode=access_mode,
        users=users,
        show_demo_credentials=_as_bool(_get(s, "access", "show_demo_credentials", "SHOW_DEMO_CREDENTIALS", False)),
        oidc_provider=str(_get(s, "access", "oidc_provider", "OIDC_PROVIDER", "")),
        allowed_emails=_list("allowed_emails"),
        allowed_domains=_list("allowed_domains"),
    )
