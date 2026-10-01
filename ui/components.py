"""Rendering helpers for chat messages, charts and the 'under the hood' tabs."""

from __future__ import annotations

import json
import re

import pandas as pd
import streamlit as st

from core import charts as chartlib
from core.catalog import Dataset, to_display_frame
from core.charts import ChartSpec, RenderedChart

from .styles import esc, md_escape

PLOTLY_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"],
    "toImageButtonOptions": {"format": "png", "scale": 2},
}


def column_config_for(df: pd.DataFrame) -> dict:
    cfg = {}
    for c in df.columns:
        lc = c.lower()
        if not pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_bool_dtype(df[c]):
            continue
        if lc.endswith(("_pct", "_percent")):
            cfg[c] = st.column_config.NumberColumn(c, format="%.1f%%")
        elif lc.endswith(("_usd", "_eur", "_gbp", "_aed")) or "amount" in lc or "cost" in lc:
            cfg[c] = st.column_config.NumberColumn(c, format="localized")
        elif pd.api.types.is_float_dtype(df[c]):
            cfg[c] = st.column_config.NumberColumn(c, format="localized")
    return cfg


def render_rendered_charts(rendered: list[RenderedChart], key_prefix: str) -> None:
    for i, chart in enumerate(rendered):
        if chart.title:
            st.markdown(f'<div class="ad-chart-title">{esc(chart.title)}</div>', unsafe_allow_html=True)
        if chart.kind == "kpi":
            cols = st.columns(len(chart.metrics))
            for col, (label, value) in zip(cols, chart.metrics):
                col.metric(label, value, border=True)
        elif chart.figure is not None:
            cfg = dict(PLOTLY_CONFIG)
            cfg["toImageButtonOptions"] = {**PLOTLY_CONFIG["toImageButtonOptions"],
                                           "filename": re.sub(r"[^a-z0-9]+", "_", chart.title.lower())[:50] or "chart"}
            st.plotly_chart(chart.figure, width="stretch", theme=None, config=cfg, key=f"{key_prefix}-chart-{i}")
        if chart.note:
            st.markdown(f'<div class="ad-note">{esc(chart.note)}</div>', unsafe_allow_html=True)


def ensure_rendered(msg: dict, dataset: Dataset) -> list[RenderedChart]:
    """Build (once) and cache the Plotly figures for a message."""
    if "_rendered" not in msg:
        df = msg.get("df")
        specs = [ChartSpec.from_dict(s) for s in msg.get("charts", [])]
        if df is None or df.empty or not specs:
            msg["_rendered"], problems = [], []
        else:
            msg["_rendered"], problems = chartlib.build_charts(specs, df, dataset.currency, dataset.as_of_date or None)
        msg["chart_problems"] = msg.get("chart_problems", []) + problems
    return msg["_rendered"]


def render_details_tabs(msg: dict, key_prefix: str) -> None:
    df: pd.DataFrame | None = msg.get("df")
    sql_tab, data_tab, trace_tab = st.tabs([":material/code: SQL query", ":material/table: Result data",
                                            ":material/account_tree: Agent trace"])
    with sql_tab:
        if msg.get("sql"):
            st.code(msg["sql"], language="sql", wrap_lines=True)
        if msg.get("reasoning"):
            st.caption(f"**SQL agent's plan:** {md_escape(msg['reasoning'])}")
        if msg.get("assumptions"):
            st.caption(f"**Assumptions:** {md_escape(msg['assumptions'])}")
        failed = msg.get("failed_attempts") or []
        if failed:
            with st.expander(f"Self-correction: {len(failed)} failed attempt(s) before this query", icon="🔧"):
                for n, attempt in enumerate(failed, 1):
                    st.markdown(f"**Attempt {n}** failed with:")
                    st.code(attempt["error"], language="text", wrap_lines=True)
                    st.code(attempt["sql"], language="sql", wrap_lines=True)
    with data_tab:
        if df is None:
            st.caption("No result set.")
        elif df.empty:
            st.caption("The query ran successfully but returned no rows.")
        else:
            st.dataframe(to_display_frame(df), hide_index=True, width="stretch",
                         height=min(420, 38 + 35 * len(df)), column_config=column_config_for(df))
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            c1.caption(f"{len(df):,} row(s)" + (" — truncated to the row limit" if msg.get("truncated") else ""))
            c2.download_button("Download CSV", df.to_csv(index=False).encode(), file_name="query_result.csv",
                               mime="text/csv", key=f"{key_prefix}-dl", icon=":material/download:", width="stretch")
    with trace_tab:
        trace = msg.get("trace") or []
        if trace:
            st.dataframe(pd.DataFrame([t.as_row() for t in trace]), hide_index=True, width="stretch",
                         column_config={"Step": st.column_config.TextColumn(width=130),
                                        "What happened": st.column_config.TextColumn(width=180),
                                        "Status": st.column_config.TextColumn(width=60),
                                        "Time (s)": st.column_config.NumberColumn(width=70),
                                        "Tokens in": st.column_config.TextColumn(width=80),
                                        "Tokens out": st.column_config.TextColumn(width=80),
                                        "Detail": st.column_config.TextColumn(width="large")})
        if msg.get("chart_reasoning"):
            st.caption(f"**Visualization agent:** {md_escape(msg['chart_reasoning'])}")
        if msg.get("chart_problems"):
            st.caption("**Chart notes:** " + md_escape("; ".join(msg["chart_problems"])))
        if msg.get("charts"):
            with st.expander("Chart specification returned by the visualization agent", icon="🎨"):
                st.code(json.dumps(msg["charts"], indent=2), language="json")


def meta_line(msg: dict) -> None:
    parts = []
    if msg.get("elapsed"):
        parts.append(f"Answered in {msg['elapsed']:.1f}s")
    df = msg.get("df")
    if df is not None:
        parts.append(f"{len(df):,} row{'s' if len(df) != 1 else ''}")
    tokens = sum((t.usage.input_tokens + t.usage.output_tokens) for t in msg.get("trace", []) if t.usage)
    if tokens:
        parts.append(f"{tokens:,} tokens")
    if msg.get("model"):
        parts.append(msg["model"])
    if parts:
        st.markdown('<div class="ad-meta">' + "".join(f"<span>{esc(p)}</span>" for p in parts) + "</div>",
                    unsafe_allow_html=True)


def render_assistant_message(msg: dict, dataset: Dataset, show_answer: bool = True) -> None:
    key_prefix = f"msg-{msg['id']}"
    kind = msg.get("kind", "answer")
    if kind == "error":
        st.error(msg.get("answer") or "Something went wrong.", icon="⚠️")
        if msg.get("sql") or msg.get("failed_attempts"):
            render_details_tabs(msg, key_prefix)
        return
    if kind == "cannot_answer":
        st.info(md_escape(msg.get("answer") or "I can't answer that from this dataset."), icon="💡")
        return
    if kind == "notice":
        st.info(md_escape(msg.get("answer", "")), icon="ℹ️")
        return

    if show_answer:
        st.markdown(md_escape(msg.get("answer", "")))
    rendered = ensure_rendered(msg, dataset)
    render_rendered_charts(rendered, key_prefix)
    render_details_tabs(msg, key_prefix)
    meta_line(msg)
