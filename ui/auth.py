"""Access control: none, simple username/password, or Streamlit's native OIDC login."""

from __future__ import annotations

import hashlib
import hmac
import time

import streamlit as st

from core.config import Settings

from .styles import esc

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


def _check_password(stored: str, supplied: str) -> bool:
    """Plain text, or 'sha256:<hex digest>' for a hashed password."""
    if stored.startswith("sha256:"):
        digest = hashlib.sha256(supplied.encode()).hexdigest()
        return hmac.compare_digest(stored.split(":", 1)[1].lower(), digest)
    return hmac.compare_digest(stored, supplied)


def current_user() -> str:
    return st.session_state.get("auth_user", "")


def is_authenticated(cfg: Settings) -> bool:
    if cfg.access_mode == "none":
        return True
    if cfg.access_mode == "oidc":
        try:
            if not st.user.is_logged_in:
                return False
        except Exception:
            return False
        email = (st.user.get("email") or "").lower()
        if cfg.allowed_emails or cfg.allowed_domains:
            domain = email.split("@")[-1] if "@" in email else ""
            if email not in cfg.allowed_emails and domain not in cfg.allowed_domains:
                return False
        st.session_state["auth_user"] = st.user.get("name") or email or "user"
        return True
    return bool(current_user())


def logout(cfg: Settings) -> None:
    st.session_state.pop("auth_user", None)
    if cfg.access_mode == "oidc":
        st.logout()
    else:
        st.rerun()


def login_page(cfg: Settings) -> None:
    """Render the login screen. The caller should st.stop() afterwards."""
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.space("large")
        with st.container(border=True):
            st.markdown(
                f'<div class="ad-login-title">{esc(cfg.app_title)}</div>'
                f'<div class="ad-login-sub">{esc(cfg.app_tagline)}</div>',
                unsafe_allow_html=True,
            )
            if cfg.access_mode == "oidc":
                _oidc_login(cfg)
            else:
                _password_login(cfg)
        if cfg.organization:
            st.caption(f"A demo by {cfg.organization}")


def _oidc_login(cfg: Settings) -> None:
    denied = False
    try:
        denied = bool(st.user.is_logged_in)  # logged in, but not on the allow-list
    except Exception:
        pass
    if denied:
        st.error("Your account isn't on the access list for this demo.")
        if st.button("Sign out", width="stretch"):
            st.logout()
        return
    if st.button("Sign in to continue", type="primary", width="stretch", icon=":material/login:"):
        try:
            if cfg.oidc_provider:
                st.login(cfg.oidc_provider)
            else:
                st.login()
        except Exception as exc:  # misconfigured [auth] secrets / missing Authlib
            st.error(f"Single sign-on isn't configured correctly: {exc}")


def _password_login(cfg: Settings) -> None:
    locked_until = st.session_state.get("auth_locked_until", 0.0)
    if time.time() < locked_until:
        st.warning(f"Too many attempts. Try again in {int(locked_until - time.time())} seconds.")
        return
    with st.form("login", border=False):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
    if cfg.show_demo_credentials and cfg.users:
        demo_user, demo_pw = next(iter(cfg.users.items()))
        if not demo_pw.startswith("sha256:"):
            st.caption(f"Demo access: username **{demo_user}** / password **{demo_pw}**")
    if submitted:
        stored = cfg.users.get(username.strip())
        if stored is not None and _check_password(stored, password):
            st.session_state["auth_user"] = username.strip()
            st.session_state["auth_attempts"] = 0
            st.rerun()
        attempts = st.session_state.get("auth_attempts", 0) + 1
        st.session_state["auth_attempts"] = attempts
        if attempts >= MAX_ATTEMPTS:
            st.session_state["auth_locked_until"] = time.time() + LOCKOUT_SECONDS
            st.session_state["auth_attempts"] = 0
        st.error("Incorrect username or password.")
