"""Sidebar: brand, dataset picker, model settings, usage and account."""

from __future__ import annotations

import streamlit as st

from core.config import PROVIDERS

from . import auth, registry, state
from .styles import esc, label


def _on_pick() -> None:
    st.session_state["dataset_id"] = st.session_state["dataset_picker"]


def render_sidebar() -> None:
    cfg = state.settings()
    datasets = state.all_datasets()
    active = state.active_dataset()

    # Apply programmatic dataset switches *before* the widget is created.
    requested = st.session_state.pop("_dataset_request", None)
    if requested in datasets:
        st.session_state["dataset_picker"] = requested
    if st.session_state.get("dataset_picker") not in datasets:
        st.session_state["dataset_picker"] = active.id

    with st.sidebar:
        initials = "".join(w[0] for w in cfg.app_title.split()[:2]).upper() or "AI"
        st.markdown(
            f'<div class="ad-brand"><div class="logo">{esc(initials)}</div><div>'
            f'<div class="name">{esc(cfg.app_title)}</div>'
            f'<div class="sub">Text-to-SQL · AI analytics demo</div></div></div>',
            unsafe_allow_html=True,
        )
        st.space("small")

        label("Dataset")
        st.selectbox("Dataset", list(datasets), format_func=lambda i: datasets[i].label, key="dataset_picker",
                     on_change=_on_pick, label_visibility="collapsed")
        ds = state.active_dataset()
        st.markdown(f'<div class="ad-dataset-blurb">{esc(ds.description)}</div>', unsafe_allow_html=True)
        if "upload" in registry.PAGES and not state.uploaded_datasets():
            st.page_link(registry.PAGES["upload"], label="Use your own CSV", icon=":material/upload_file:")

        st.space("small")
        _model_settings()

        limit = cfg.max_questions_per_session
        if limit > 0:
            used = state.questions_used()
            st.progress(min(1.0, used / limit), text=f"{used} of {limit} questions used this session")

        if cfg.access_mode != "none" and auth.current_user():
            st.space("small")
            c1, c2 = st.columns([3, 2], vertical_alignment="center")
            c1.caption(f"Signed in as **{auth.current_user()}**")
            if c2.button("Sign out", key="logout", width="stretch"):
                auth.logout(cfg)

        links = []
        if cfg.organization:
            links.append(f'<a href="{esc(cfg.organization_url)}" target="_blank">{esc(cfg.organization)}</a>'
                         if cfg.organization_url else esc(cfg.organization))
        if cfg.repo_url:
            links.append(f'<a href="{esc(cfg.repo_url)}" target="_blank">Source on GitHub</a>')
        links.append("All demo data is synthetic")
        st.markdown('<div class="ad-footer">' + " · ".join(links) + "</div>", unsafe_allow_html=True)


def _model_settings() -> None:
    cfg = state.settings()
    if cfg.offline:
        with st.expander("Model settings", icon=":material/tune:"):
            st.caption("**Offline replay mode.** Pre-written SQL and chart specs are replayed for the example "
                       "questions - no AI calls are made. Set `LLM_PROVIDER` to `anthropic` or `openai` to "
                       "use a real model.")
        return

    provider = state.current_provider()
    key, source = state.api_key_for(provider)
    with st.expander("Model settings", icon=":material/tune:", expanded=not key):
        if cfg.allow_model_choice:
            providers = list(PROVIDERS)
            st.radio("AI provider", providers, format_func=PROVIDERS.get, key="llm_provider",
                     index=providers.index(provider), horizontal=True)
            provider = state.current_provider()
            options = state.model_options(provider)
            current = state.current_model()
            choices = list(options) + ([current] if current not in options else [])
            st.selectbox("Model", choices, format_func=lambda m: options.get(m, m), key=f"llm_model_{provider}",
                         index=choices.index(current), help="Sonnet is the best balance of speed and quality; "
                         "Haiku is fastest; Opus is the most capable." if provider == "anthropic" else None)
        else:
            st.caption(f"Model: **{state.current_model()}**")

        key, source = state.api_key_for(provider)
        if source == "server":
            st.caption("✅ Using the app's API key.")
        elif cfg.allow_user_api_keys:
            st.text_input(
                f"{PROVIDERS[provider].split(' ')[0]} API key", type="password", key=f"user_api_key_{provider}",
                placeholder="sk-…", help="Kept in your browser session only - never stored or logged.",
            )
            if not st.session_state.get(f"user_api_key_{provider}"):
                st.caption("Bring your own key to try the demo.")
        else:
            st.caption("No API key is configured for this provider.")
