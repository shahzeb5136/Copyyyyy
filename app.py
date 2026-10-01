"""
Ask Your Data - an open-source demo of LLMs working with structured data.

    streamlit run app.py

Ask a question in plain English -> an SQL agent writes a query -> the query runs
in a sandboxed SQL engine -> an analyst agent explains the result and a
visualization agent designs a chart that Python renders.
"""

from __future__ import annotations

import logging

import streamlit as st

from ui import auth, registry, state
from ui.sidebar import render_sidebar
from ui.styles import inject_css
from ui.views import about, ask, catalog, upload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

cfg = state.settings()
st.set_page_config(
    page_title=cfg.app_title,
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": f"**{cfg.app_title}** - {cfg.app_tagline}"},
)
inject_css()

if not auth.is_authenticated(cfg):
    auth.login_page(cfg)
    st.stop()

pages = {
    "ask": st.Page(ask.render, title="Ask", icon=":material/chat:", url_path="ask", default=True),
    "catalog": st.Page(catalog.render, title="Data catalog", icon=":material/dataset:", url_path="catalog"),
    "upload": st.Page(upload.render, title="Bring your own data", icon=":material/upload_file:", url_path="upload"),
    "about": st.Page(about.render, title="How it works", icon=":material/lightbulb:", url_path="how-it-works"),
}
registry.PAGES.update(pages)

nav = st.navigation(list(pages.values()), position="top")
render_sidebar()
nav.run()
