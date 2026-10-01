"""💡 How it works - architecture, guardrails and design choices."""

from __future__ import annotations

import streamlit as st

from .. import state
from ..styles import PIPELINE_STEPS, hero, label, steps

FLOW = """
<div class="ad-flow-wrap"><div class="ad-flow">
  <div class="col">
    <div class="node">Question<small>plain English</small></div>
    <div class="node input">Data dictionary<small>schema + definitions</small></div>
  </div>
  <div class="arrow">→</div>
  <div class="col">
    <div class="node llm">SQL agent<small>LLM · writes one SELECT</small></div>
    <div class="loop">↻ repairs its SQL if the database rejects it</div>
  </div>
  <div class="arrow">→</div>
  <div class="col"><div class="node">Sandboxed SQL engine<small>DuckDB over CSV</small></div></div>
  <div class="arrow">→</div>
  <div class="col">
    <div class="node llm">Analyst agent<small>LLM · reads the rows, writes the answer</small></div>
    <div class="lane">
      <div class="node llm">Visualization agent<small>LLM · chart spec (JSON)</small></div>
      <div class="arrow">→</div>
      <div class="node">Chart renderer<small>Python + Plotly</small></div>
    </div>
  </div>
  <div class="arrow">→</div>
  <div class="col"><div class="node out">Answer + chart<small>+ SQL + data</small></div></div>
</div></div>
"""


def render() -> None:
    cfg = state.settings()
    hero("How it works", "LLMs + structured data",
         "Large language models aren't only for text. Here three cooperating agents turn a question into SQL, "
         "run it, explain the result and design a chart for it.")
    steps(PIPELINE_STEPS)

    st.markdown("".join(line.strip() for line in FLOW.splitlines()), unsafe_allow_html=True)

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
