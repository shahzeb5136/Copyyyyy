"""Session state, caching and service factories shared by every page."""

from __future__ import annotations

import logging

import streamlit as st

from core import catalog
from core.config import ANTHROPIC_MODELS, DEFAULT_MODEL, OPENAI_MODELS, Settings, load_settings
from core.database import QueryEngine
from core.llm import BaseLLM, LLMError, make_llm
from core.report import Report

log = logging.getLogger("askdata")


# --------------------------------------------------------------------------- #
# Settings & built-in data (shared across all sessions)
# --------------------------------------------------------------------------- #
def settings() -> Settings:
    if "_settings" not in st.session_state:
        st.session_state["_settings"] = load_settings()
    return st.session_state["_settings"]


@st.cache_resource(show_spinner="Loading demo datasets…")
def builtin_datasets() -> dict[str, catalog.Dataset]:
    return catalog.load_builtin_datasets()


@st.cache_resource(show_spinner=False, max_entries=10)
def _builtin_engine(dataset_id: str, fingerprint: str, max_rows: int, timeout: float) -> QueryEngine:
    return QueryEngine(builtin_datasets()[dataset_id], max_rows=max_rows, timeout_seconds=timeout)


@st.cache_data(show_spinner=False, max_entries=10)
def _builtin_schema_text(dataset_id: str, fingerprint: str) -> str:
    return catalog.schema_for_prompt(builtin_datasets()[dataset_id])


# --------------------------------------------------------------------------- #
# Datasets (built-in + uploaded in this session)
# --------------------------------------------------------------------------- #
def uploaded_datasets() -> dict[str, catalog.Dataset]:
    return st.session_state.setdefault("uploaded_datasets", {})


def all_datasets() -> dict[str, catalog.Dataset]:
    return {**builtin_datasets(), **uploaded_datasets()}


def active_dataset() -> catalog.Dataset:
    datasets = all_datasets()
    ds_id = st.session_state.get("dataset_id")
    if ds_id not in datasets:
        ds_id = next(iter(datasets))
        st.session_state["dataset_id"] = ds_id
    return datasets[ds_id]


def set_active_dataset(dataset_id: str) -> None:
    """Switch dataset programmatically. The sidebar picker picks this up on the next run."""
    st.session_state["dataset_id"] = dataset_id
    st.session_state["_dataset_request"] = dataset_id


def engine_for(ds: catalog.Dataset) -> QueryEngine:
    cfg = settings()
    if ds.source == "builtin":
        return _builtin_engine(ds.id, ds.fingerprint, cfg.max_result_rows, cfg.query_timeout_seconds)
    engines = st.session_state.setdefault("_upload_engines", {})
    key = (ds.id, ds.fingerprint)
    if key not in engines:
        for old_key in [k for k in engines if k[0] == ds.id]:
            engines.pop(old_key).close()
        engines[key] = QueryEngine(ds, max_rows=cfg.max_result_rows, timeout_seconds=cfg.query_timeout_seconds)
    return engines[key]


def schema_text_for(ds: catalog.Dataset) -> str:
    if ds.source == "builtin":
        return _builtin_schema_text(ds.id, ds.fingerprint)
    cache = st.session_state.setdefault("_upload_schema_text", {})
    key = (ds.id, ds.fingerprint)
    if key not in cache:
        cache[key] = catalog.schema_for_prompt(ds)
    return cache[key]


def add_uploaded_dataset(ds: catalog.Dataset) -> None:
    uploaded_datasets()[ds.id] = ds
    chats().pop(ds.id, None)
    histories().pop(ds.id, None)
    reports().pop(ds.id, None)


def remove_uploaded_dataset(dataset_id: str) -> None:
    uploaded_datasets().pop(dataset_id, None)
    chats().pop(dataset_id, None)
    histories().pop(dataset_id, None)
    reports().pop(dataset_id, None)
    engines = st.session_state.get("_upload_engines", {})
    for key in [k for k in engines if k[0] == dataset_id]:
        engines.pop(key).close()
    if st.session_state.get("dataset_id") == dataset_id:
        set_active_dataset(next(iter(builtin_datasets())))


# --------------------------------------------------------------------------- #
# Conversation state (one thread per dataset)
# --------------------------------------------------------------------------- #
def chats() -> dict[str, list[dict]]:
    return st.session_state.setdefault("chats", {})


def chat_for(dataset_id: str) -> list[dict]:
    return chats().setdefault(dataset_id, [])


def histories() -> dict[str, list[dict]]:
    return st.session_state.setdefault("histories", {})


def history_for(dataset_id: str) -> list[dict]:
    return histories().setdefault(dataset_id, [])


def clear_chat(dataset_id: str) -> None:
    chats()[dataset_id] = []
    histories()[dataset_id] = []
    reports().pop(dataset_id, None)


def reports() -> dict[str, Report]:
    """The latest generated report per dataset (reused until the conversation changes)."""
    return st.session_state.setdefault("reports", {})


def questions_used() -> int:
    return int(st.session_state.get("questions_used", 0))


def increment_questions() -> None:
    st.session_state["questions_used"] = questions_used() + 1


def questions_left() -> int | None:
    limit = settings().max_questions_per_session
    return None if limit <= 0 else max(0, limit - questions_used())


# --------------------------------------------------------------------------- #
# LLM selection
# --------------------------------------------------------------------------- #
def model_options(provider: str) -> dict[str, str]:
    return ANTHROPIC_MODELS if provider == "anthropic" else OPENAI_MODELS


def current_provider() -> str:
    cfg = settings()
    if cfg.offline:
        return "offline"
    return st.session_state.get("llm_provider", cfg.provider)


def current_model() -> str:
    cfg = settings()
    provider = current_provider()
    if provider == "offline":
        return "offline-replay"
    if not cfg.allow_model_choice:
        return cfg.model if provider == cfg.provider else DEFAULT_MODEL[provider]
    default = cfg.model if provider == cfg.provider else DEFAULT_MODEL[provider]
    return st.session_state.get(f"llm_model_{provider}", default) or default


def api_key_for(provider: str) -> tuple[str, str]:
    """Return (api_key, source) where source is 'server', 'user' or ''."""
    cfg = settings()
    server_key = cfg.api_key_for(provider)
    if server_key:
        return server_key, "server"
    if cfg.allow_user_api_keys:
        user_key = (st.session_state.get(f"user_api_key_{provider}") or "").strip()
        if user_key:
            return user_key, "user"
    return "", ""


def get_llm() -> tuple[BaseLLM | None, str]:
    """Build an LLM client for the current selection, or return (None, reason)."""
    provider = current_provider()
    key, _source = api_key_for(provider)
    if not key:
        if settings().allow_user_api_keys:
            return None, "Add an API key in the sidebar (⚙️ Model settings) to start asking questions."
        return None, "This demo has no AI provider configured yet. Please check back later."
    try:
        return make_llm(provider, key, current_model(), effort=settings().effort), ""
    except LLMError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to create LLM client")
        return None, f"Couldn't initialise the AI client: {exc}"
