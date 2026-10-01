"""Custom CSS and small HTML building blocks (light theme)."""

from __future__ import annotations

import html

import streamlit as st

CSS = """
<style>
:root {
  --ad-ink: #111827;
  --ad-ink-2: #374151;
  --ad-muted: #6b7280;
  --ad-border: #e5e7eb;
  --ad-surface: #f6f8fb;
  --ad-primary: #2a6fdb;
  --ad-primary-soft: #eaf1fd;
  --ad-good: #0f7b38;
  --ad-good-soft: #e7f6ec;
}

/* Layout */
.block-container { padding-top: 2.2rem; padding-bottom: 6rem; max-width: 1180px; }
section[data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }

/* Headings */
h1, h2, h3 { letter-spacing: -0.015em; color: var(--ad-ink); }

/* Hero */
.ad-hero {
  border: 1px solid var(--ad-border);
  background: radial-gradient(1200px 300px at 0% 0%, #eef4ff 0%, #ffffff 60%);
  border-radius: 16px;
  padding: 22px 26px 20px 26px;
  margin-bottom: 18px;
}
.ad-eyebrow { font-size: 0.78rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--ad-primary); margin-bottom: 6px; }
.ad-hero h1 { font-size: 1.85rem; margin: 0 0 6px 0; padding: 0; line-height: 1.2; }
.ad-hero p { color: var(--ad-ink-2); margin: 0; font-size: 1.0rem; max-width: 760px; }
.ad-chips { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px; }
.ad-chip { display: inline-flex; align-items: center; gap: 6px; font-size: 0.8rem; color: var(--ad-ink-2);
  background: #fff; border: 1px solid var(--ad-border); border-radius: 999px; padding: 3px 10px; }
.ad-chip b { color: var(--ad-ink); font-weight: 600; }

/* Pipeline steps */
.ad-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 6px 0 18px 0; }
.ad-step { border: 1px solid var(--ad-border); border-radius: 12px; padding: 12px 14px; background: #fff; }
.ad-step .n { display: inline-flex; width: 22px; height: 22px; border-radius: 999px; align-items: center;
  justify-content: center; background: var(--ad-primary-soft); color: var(--ad-primary); font-weight: 700;
  font-size: 0.75rem; margin-bottom: 6px; }
.ad-step .t { font-weight: 600; color: var(--ad-ink); font-size: 0.92rem; }
.ad-step .d { color: var(--ad-muted); font-size: 0.82rem; line-height: 1.35; margin-top: 2px; }
@media (max-width: 760px) { .ad-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

/* Section label */
.ad-label { font-size: 0.78rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  color: var(--ad-muted); margin: 6px 0 8px 0; }

/* Example question buttons: left aligned cards */
.st-key-examples button { justify-content: flex-start; text-align: left; min-height: 3.1rem;
  background: #fff; border: 1px solid var(--ad-border); }
.st-key-examples button:hover { border-color: var(--ad-primary); color: var(--ad-primary); background: #fbfdff; }
.st-key-examples button p { font-size: 0.92rem; text-align: left; }
.st-key-examples button > div { justify-content: flex-start; width: 100%; }

/* Chart title + notes */
.ad-chart-title { font-weight: 600; font-size: 1.0rem; color: var(--ad-ink); margin: 10px 0 0 2px; }
.ad-note { color: var(--ad-muted); font-size: 0.8rem; margin: -4px 0 6px 2px; }

/* Answer meta line */
.ad-meta { color: var(--ad-muted); font-size: 0.8rem; margin-top: 2px; }
.ad-meta span + span::before { content: "·"; margin: 0 6px; color: #c0c4cc; }

/* Sidebar brand */
.ad-brand { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.ad-brand .logo { width: 34px; height: 34px; border-radius: 10px; display: flex; align-items: center;
  justify-content: center; background: linear-gradient(135deg, #2a6fdb, #5b8ff0); color: white; font-weight: 700; }
.ad-brand .name { font-weight: 700; font-size: 1.05rem; color: var(--ad-ink); line-height: 1.1; }
.ad-brand .sub { font-size: 0.75rem; color: var(--ad-muted); }
.ad-dataset-blurb { font-size: 0.82rem; color: var(--ad-muted); line-height: 1.4; margin-top: -4px; }

/* Login */
.ad-login-title { text-align: center; margin: 4px 0 2px 0; font-size: 1.5rem; font-weight: 700; color: var(--ad-ink); }
.ad-login-sub { text-align: center; color: var(--ad-muted); margin-bottom: 16px; font-size: 0.95rem; }

/* Footer */
.ad-footer { color: var(--ad-muted); font-size: 0.78rem; margin-top: 10px; }
.ad-footer a { color: var(--ad-muted); }

/* Code blocks a touch smaller */
[data-testid="stCode"] code { font-size: 0.82rem; }

/* Hide the default "Deploy" button for a cleaner public demo */
[data-testid="stAppDeployButton"] { display: none; }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def hero(eyebrow: str, title: str, subtitle: str, chips: list[tuple[str, str]] | None = None) -> None:
    chips_html = ""
    if chips:
        chips_html = '<div class="ad-chips">' + "".join(
            f'<span class="ad-chip">{esc(k)} <b>{esc(v)}</b></span>' for k, v in chips) + "</div>"
    st.markdown(
        f'<div class="ad-hero"><div class="ad-eyebrow">{esc(eyebrow)}</div>'
        f"<h1>{esc(title)}</h1><p>{esc(subtitle)}</p>{chips_html}</div>",
        unsafe_allow_html=True,
    )


def steps(items: list[tuple[str, str]]) -> None:
    cards = "".join(
        f'<div class="ad-step"><div class="n">{i}</div><div class="t">{esc(t)}</div><div class="d">{esc(d)}</div></div>'
        for i, (t, d) in enumerate(items, 1)
    )
    st.markdown(f'<div class="ad-steps">{cards}</div>', unsafe_allow_html=True)


def label(text: str) -> None:
    st.markdown(f'<div class="ad-label">{esc(text)}</div>', unsafe_allow_html=True)


def md_escape(text: str) -> str:
    """Escape characters that Streamlit markdown would otherwise interpret (e.g. $ as LaTeX)."""
    return (text or "").replace("$", "\\$")


PIPELINE_STEPS = [
    ("Ask", "Type a business question in plain English."),
    ("SQL agent", "An LLM reads the data dictionary and writes a SQL query."),
    ("Database", "The query runs in a sandboxed SQL engine over the data."),
    ("Analyst + viz agents", "One LLM explains the result; another designs the chart."),
]
