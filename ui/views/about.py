"""💡 How it works - architecture, guardrails and design choices."""

from __future__ import annotations

import streamlit as st

from .. import state
from ..styles import PIPELINE_STEPS, hero, label, steps

FLOW = """
digraph G {
  rankdir=LR; bgcolor="transparent"; pad=0.1; nodesep=0.25; ranksep=0.35;
  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=17, color="#d6d9de",
        fillcolor="#ffffff", fontcolor="#1f2937", margin="0.14,0.08", penwidth=1.4];
  edge [color="#9aa3af", arrowsize=0.8, fontname="Helvetica", fontsize=13, fontcolor="#6b7280", penwidth=1.3];

  q    [label="Question\\n(plain English)", fillcolor="#eaf1fd", color="#2a6fdb"];
  dict [label="Data dictionary\\n(schema + definitions)", fillcolor="#f6f8fb"];
  sql  [label="SQL agent\\n(LLM)", fillcolor="#eaf1fd", color="#2a6fdb"];
  db   [label="Sandboxed SQL engine\\n(DuckDB over CSV)", fillcolor="#f6f8fb"];
  ans  [label="Analyst agent\\n(LLM)", fillcolor="#eaf1fd", color="#2a6fdb"];
  viz  [label="Visualization agent\\n(LLM)", fillcolor="#eaf1fd", color="#2a6fdb"];
  plot [label="Chart renderer\\n(Python + Plotly)", fillcolor="#f6f8fb"];
  out  [label="Answer + chart\\n+ SQL + data", fillcolor="#e7f6ec", color="#0f7b38"];

  q -> sql; dict -> sql [style=dashed];
  sql -> db [label="SELECT …"];
  db -> sql [label="error → retry", style=dashed, constraint=false];
  db -> ans [label="rows"]; db -> viz [label="result profile"];
  viz -> plot [label="chart spec (JSON)"];
  ans -> out; plot -> out;
}
"""


def render() -> None:
    cfg = state.settings()
    hero("How it works", "LLMs + structured data",
         "Large language models aren't only for text. Here three cooperating agents turn a question into SQL, "
         "run it, explain the result and design a chart for it.")
    steps(PIPELINE_STEPS)

    st.graphviz_chart(FLOW, width="stretch")

    c1, c2 = st.columns(2, gap="large")
    with c1:
        label("The agents")
        st.markdown(
            "- **SQL agent** - receives the question, the recent conversation and the *data dictionary* "
            "(tables, columns, types, value profiles and plain-English definitions) and returns one DuckDB "
            "`SELECT` statement as structured JSON. If the database rejects the query, the error is fed back "
            "and the agent repairs its own SQL (up to two retries).\n"
            "- **Analyst agent** - reads the actual result rows and writes a short, numbers-first answer. It is "
            "instructed to use only figures present in the result.\n"
            "- **Visualization agent** - looks at the question and a profile of the result and picks the chart "
            "type (bar, line, Gantt, heatmap, treemap, KPI tiles…) and which columns go where. It runs in "
            "parallel with the analyst while the answer streams in."
        )
        label("Why a chart spec instead of generated code?")
        st.markdown(
            "The visualization agent returns a small JSON specification, and trusted Python code (Plotly) "
            "renders it. Executing model-written code in a public app would be a security risk; a spec keeps "
            "the AI in charge of *what* to show while the app stays in charge of *how* - with a consistent, "
            "colour-blind-safe style. The spec is validated against the real result columns, and an automatic "
            "fallback chart is used if it doesn't fit."
        )
    with c2:
        label("Guardrails")
        st.markdown(
            f"- Only a **single read-only `SELECT`** is executed - checked by the database's own parser, not "
            f"by string matching.\n"
            f"- The SQL engine runs **in memory with file-system and network access disabled** and its "
            f"configuration locked, so generated SQL can't read files, secrets or install extensions.\n"
            f"- Each query has a **{cfg.query_timeout_seconds:.0f}s timeout** and results are capped at "
            f"**{cfg.max_result_rows:,} rows**.\n"
            f"- Structured outputs (JSON schema) make every agent response machine-checkable.\n"
            + (f"- Each session can ask up to **{cfg.max_questions_per_session} questions** to keep costs "
               f"predictable.\n" if cfg.max_questions_per_session > 0 else "")
            + "- Uploaded files stay in the browser session and are never written to disk."
        )
        label("Why there's no real database")
        st.markdown(
            "For a self-contained demo the CSV files are loaded into an in-memory **DuckDB** engine - a real "
            "SQL database, so joins, CTEs, window functions and date maths all work exactly as they would "
            "against a warehouse. Pointing the SQL agent at Postgres, Snowflake, BigQuery or SQL Server only "
            "means swapping the engine class and the SQL dialect hint."
        )
        label("Tech stack")
        st.markdown("Streamlit · DuckDB · Plotly · pandas · Anthropic Claude or OpenAI (structured outputs)")
    if cfg.repo_url:
        st.link_button("View the source on GitHub", cfg.repo_url, icon=":material/code:")
