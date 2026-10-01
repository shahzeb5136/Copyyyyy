"""The agent pipeline: question -> SQL -> results -> answer + visuals.

Each step is a plain function so the UI can show progress between steps and the
whole flow can be tested with a fake LLM.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pandas as pd

from . import prompts
from .catalog import Dataset, schema_for_prompt
from .charts import ChartSpec, is_datetime, is_numeric
from .database import QueryEngine, QueryError, QueryResult
from .llm import BaseLLM, LLMError, Usage


@dataclass
class TraceStep:
    agent: str
    action: str
    elapsed_ms: float
    status: str = "ok"  # ok | error | skipped
    detail: str = ""
    usage: Usage | None = None

    def as_row(self) -> dict:
        return {
            "Step": self.agent,
            "What happened": self.action,
            "Status": {"ok": "✅", "error": "⚠️", "skipped": "➖"}.get(self.status, self.status),
            "Time (s)": round(self.elapsed_ms / 1000, 2),
            "Tokens in": f"{self.usage.input_tokens:,}" if self.usage else "–",
            "Tokens out": f"{self.usage.output_tokens:,}" if self.usage else "–",
            "Detail": self.detail,
        }


@dataclass
class SQLOutcome:
    question: str
    can_answer: bool = True
    sql: str = ""
    reasoning: str = ""
    assumptions: str = ""
    result: QueryResult | None = None
    error: str = ""
    failed_attempts: list[dict] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.can_answer and self.result is not None and not self.error


# --------------------------------------------------------------------------- #
# Step 1 + 2: write SQL and run it (with self-repair)
# --------------------------------------------------------------------------- #
def run_sql_stage(
    question: str,
    dataset: Dataset,
    engine: QueryEngine,
    llm: BaseLLM,
    history: list[dict] | None = None,
    max_repairs: int = 2,
    on_event: Callable[[str], None] | None = None,
    schema_text: str | None = None,
) -> SQLOutcome:
    emit = on_event or (lambda _msg: None)
    out = SQLOutcome(question=question)
    schema_text = schema_text or schema_for_prompt(dataset)

    for attempt in range(max_repairs + 1):
        emit("🧠 SQL agent is writing a query…" if attempt == 0 else f"🔧 Query failed - SQL agent is fixing it (retry {attempt})…")
        user = prompts.sql_user_prompt(schema_text, question, history or [], out.failed_attempts)
        try:
            res = llm.generate_json(system=prompts.SQL_SYSTEM, user=user, schema=prompts.SQL_SCHEMA,
                                    schema_name="sql_query")
        except LLMError as exc:
            out.error = str(exc)
            out.trace.append(TraceStep("SQL agent", "Generate SQL", 0, "error", str(exc)))
            return out
        data = res.data
        out.reasoning = str(data.get("reasoning", "")).strip()
        out.assumptions = str(data.get("assumptions", "")).strip()
        out.can_answer = bool(data.get("can_answer", True))
        out.sql = str(data.get("sql", "")).strip()
        out.trace.append(TraceStep("SQL agent", "Generate SQL" if attempt == 0 else f"Repair SQL (attempt {attempt})",
                                   res.elapsed_ms, usage=res.usage, detail=out.reasoning))
        if not out.can_answer or not out.sql:
            out.can_answer = False
            return out

        emit("🗄️ Running the query against the database…")
        start = time.perf_counter()
        try:
            out.result = engine.execute(out.sql)
            out.sql = out.result.sql
            detail = f"{out.result.row_count:,} rows" + (" (truncated)" if out.result.truncated else "")
            out.trace.append(TraceStep("Database", "Execute SQL (DuckDB)", out.result.elapsed_ms, detail=detail))
            return out
        except QueryError as exc:
            elapsed = (time.perf_counter() - start) * 1000
            out.trace.append(TraceStep("Database", "Execute SQL (DuckDB)", elapsed, "error", str(exc)))
            out.failed_attempts.append({"sql": out.sql, "error": str(exc)})
            out.result = None
            if exc.kind == "timeout":
                out.error = str(exc)
                return out

    out.error = ("I couldn't produce a query that runs successfully. "
                 f"Last database error: {out.failed_attempts[-1]['error'] if out.failed_attempts else 'unknown'}")
    return out


# --------------------------------------------------------------------------- #
# Result serialisation for the downstream agents
# --------------------------------------------------------------------------- #
def _serialisable(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if is_datetime(out[c]):
            col = out[c].dropna()
            fmt = "%Y-%m-%d" if len(col) == 0 or (col.dt.normalize() == col).all() else "%Y-%m-%d %H:%M"
            out[c] = out[c].dt.strftime(fmt)
        elif pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(2)
    return out


def result_for_analyst(result: QueryResult, max_rows: int = 80) -> str:
    df = result.df
    lines = [f"rows_returned: {len(df):,}" + (" (TRUNCATED - more rows exist)" if result.truncated else "")]
    if df.empty:
        lines.append("(no rows)")
        return "\n".join(lines)
    lines.append(_serialisable(df.head(max_rows)).to_csv(index=False).strip())
    if len(df) > max_rows:
        lines.append(f"... {len(df) - max_rows:,} more rows not shown.")
        num = [c for c in df.columns if is_numeric(df[c])]
        if num:
            stats = df[num].agg(["sum", "mean", "min", "max"]).T.round(2)
            lines.append("Column statistics over ALL returned rows:")
            lines.append(stats.to_csv().strip())
    return "\n".join(lines)


def result_profile(result: QueryResult, sample_rows: int = 12) -> str:
    df = result.df
    lines = [f"rows: {len(df):,}" + (" (truncated)" if result.truncated else ""), "columns:"]
    for c in df.columns:
        s = df[c]
        if is_numeric(s):
            kind = "numeric"
            extra = f"min={s.min():,.4g}, max={s.max():,.4g}" if s.notna().any() else "all null"
        elif is_datetime(s):
            kind = "date"
            extra = f"from {s.min():%Y-%m-%d} to {s.max():%Y-%m-%d}" if s.notna().any() else "all null"
        elif pd.api.types.is_bool_dtype(s):
            kind, extra = "boolean", ""
        else:
            kind = "text"
            extra = f"{s.nunique():,} distinct"
        lines.append(f"  - {c} ({kind}; {extra})")
    lines.append(f"first {min(sample_rows, len(df))} rows:")
    lines.append(_serialisable(df.head(sample_rows)).to_csv(index=False).strip())
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Step 3: the analyst agent (streamed)
# --------------------------------------------------------------------------- #
def stream_answer(outcome: SQLOutcome, dataset: Dataset, llm: BaseLLM) -> Iterator[str]:
    assert outcome.result is not None
    user = prompts.analyst_user_prompt(
        outcome.question, outcome.sql, outcome.assumptions, result_for_analyst(outcome.result),
        dataset.as_of_date, dataset.currency,
    )
    yield from llm.stream_text(system=prompts.ANALYST_SYSTEM, user=user, max_tokens=6_000)


# --------------------------------------------------------------------------- #
# Step 4: the visualization agent
# --------------------------------------------------------------------------- #
@dataclass
class VizOutcome:
    specs: list[ChartSpec]
    reasoning: str
    trace: TraceStep


def design_charts(outcome: SQLOutcome, dataset: Dataset, llm: BaseLLM) -> VizOutcome:
    result = outcome.result
    if result is None or result.df.empty:
        return VizOutcome([], "No rows to visualise.", TraceStep("Visualization agent", "Design charts", 0, "skipped",
                                                                  "No rows returned"))
    user = prompts.viz_user_prompt(outcome.question, outcome.sql, result_profile(result), dataset.currency)
    try:
        res = llm.generate_json(system=prompts.VIZ_SYSTEM, user=user, schema=prompts.VIZ_SCHEMA,
                                schema_name="chart_plan", max_tokens=6_000)
    except LLMError as exc:
        return VizOutcome([], "", TraceStep("Visualization agent", "Design charts", 0, "error", str(exc)))
    specs = [ChartSpec.from_dict(c) for c in res.data.get("charts", []) if isinstance(c, dict)]
    reasoning = str(res.data.get("reasoning", "")).strip()
    kinds = ", ".join(s.chart_type for s in specs) or "no chart"
    return VizOutcome(specs, reasoning, TraceStep("Visualization agent", "Design charts", res.elapsed_ms,
                                                  usage=res.usage, detail=f"Chose: {kinds}. {reasoning}"))


# --------------------------------------------------------------------------- #
# Conversation memory
# --------------------------------------------------------------------------- #
def history_entry(outcome: SQLOutcome, answer: str = "") -> dict:
    summary = ""
    if outcome.result is not None:
        cols = ", ".join(outcome.result.df.columns[:12])
        summary = f"{outcome.result.row_count} rows; columns: {cols}"
    if answer:
        summary += f". Answer given: {answer[:300]}"
    return {"question": outcome.question, "sql": outcome.sql, "summary": summary}


# --------------------------------------------------------------------------- #
# Bring-your-own-data helper: draft column definitions
# --------------------------------------------------------------------------- #
def draft_dictionary(table_name: str, df: pd.DataFrame, column_types: dict[str, str], llm: BaseLLM) -> dict:
    """Ask the LLM to propose a table description and column descriptions."""
    lines = [f"Table name: {table_name}", f"Rows: {len(df):,}", "Columns:"]
    for c in df.columns:
        sample = df[c].dropna().astype(str).drop_duplicates().head(6).tolist()
        lines.append(f"  - {c} ({column_types.get(c, 'text')}): samples {sample}")
    res = llm.generate_json(system=prompts.DICTIONARY_SYSTEM, user="\n".join(lines),
                            schema=prompts.DICTIONARY_SCHEMA, schema_name="data_dictionary", max_tokens=6_000)
    cols = {str(c.get("name", "")).strip(): str(c.get("description", "")).strip()
            for c in res.data.get("columns", []) if isinstance(c, dict)}
    return {"table_description": str(res.data.get("table_description", "")).strip(), "columns": cols,
            "usage": res.usage}
