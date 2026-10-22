"""Ask any question about the data: plan steps -> execute -> explain, with self-repair."""

from __future__ import annotations

import json
import logging

import pandas as pd
from pydantic import BaseModel, Field

from ..engine.charts import render_with_fallback
from ..engine.pipeline import QueryError, run_steps
from ..engine.profile import TableProfile, frame_to_records
from ..llm_client import LLMClient, LLMError
from ..spec.models import AskPlan, ChartConfig, SemanticModel, Step, Widget, slugify
from . import prompts
from .structured import call_structured

log = logging.getLogger("dashboard.inference")


class AskResult(BaseModel):
    question: str
    can_answer: bool = True
    answer: str = ""
    assumptions: str = ""
    reasoning: str = ""
    steps: list[Step] = Field(default_factory=list)
    chart: ChartConfig = Field(default_factory=ChartConfig)
    kind: str = ""
    figure: dict | None = None
    kpi: dict | None = None
    table: dict | None = None
    row_count: int = 0
    note: str = ""
    widget: Widget | None = None  # ready to be added to the dashboard
    summary: str = ""  # for conversation memory


def _result_text(df: pd.DataFrame, max_rows: int = 80) -> str:
    rec = frame_to_records(df, max_rows)
    lines = [f"rows_returned: {rec['row_count']:,}" + (f" (showing the first {max_rows})" if rec["truncated"] else "")]
    if df.empty:
        lines.append("(no rows)")
        return "\n".join(lines)
    lines.append(",".join(rec["columns"]))
    for row in rec["rows"]:
        lines.append(",".join("" if v is None else str(v) for v in row))
    if rec["truncated"]:
        num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        if num:
            stats = df[num].agg(["sum", "mean", "min", "max"]).T.round(2)
            lines.append("statistics over ALL rows:")
            lines.append(stats.to_csv().strip())
    return "\n".join(lines)


def answer_question(df: pd.DataFrame, profile: TableProfile, model: SemanticModel, question: str,
                    history: list[dict], llm: LLMClient | None, max_table_rows: int = 500,
                    max_repairs: int = 2, progress=None) -> AskResult:
    out = AskResult(question=question)
    if llm is None:
        out.can_answer = False
        out.answer = "No AI model is configured, so questions cannot be answered. Set LLM_API_KEY and restart the app."
        return out
    failed: list[dict] = []
    data: pd.DataFrame | None = None
    plan: AskPlan | None = None
    for attempt in range(max_repairs + 1):
        if progress:
            progress("Planning how to answer…" if attempt == 0 else f"Fixing the plan (retry {attempt})…")
        res = call_structured(llm, system=prompts.ASK_PLAN_SYSTEM,
                              user=prompts.ask_plan_prompt(profile, model, history, question, failed),
                              model_cls=AskPlan, schema_name="ask_plan", max_attempts=2)
        plan = res.obj  # type: ignore[assignment]
        out.assumptions, out.reasoning = plan.assumptions, plan.reasoning
        if not plan.can_answer:
            out.can_answer = False
            out.answer = plan.assumptions or "This question can't be answered from the uploaded data."
            return out
        try:
            data = run_steps(df, plan.steps).df
            break
        except QueryError as exc:
            failed.append({"plan": json.dumps([s.model_dump(mode="json", exclude_defaults=True) for s in plan.steps]),
                           "error": str(exc)})
            data = None
    if data is None or plan is None:
        out.can_answer = False
        out.answer = ("I couldn't produce a calculation that runs on this data. Last error: "
                      + (failed[-1]["error"] if failed else "unknown"))
        return out
    out.steps, out.chart, out.row_count = plan.steps, plan.chart, int(len(data))
    if progress:
        progress("Writing the answer…")
    try:
        resp = llm.complete_text(system=prompts.ASK_ANSWER_SYSTEM,
                                 user=prompts.ask_answer_prompt(question, plan.assumptions, _result_text(data), model),
                                 max_tokens=1_500)
        out.answer = resp.text.strip()
    except LLMError as exc:
        out.answer = f"The calculation ran but the written answer failed ({exc}). See the table below."
    if not data.empty:
        fig, _ = render_with_fallback(plan.chart, data.head(5_000), model.currency, max_table_rows)
        out.kind, out.figure, out.kpi, out.table, out.note = fig.kind, fig.figure, fig.kpi, fig.table, fig.note
        if plan.chart.type != "table" and fig.kind != "table":
            out.table = frame_to_records(data, max_table_rows)  # always offer the numbers behind the chart
    else:
        out.kind, out.table = "table", frame_to_records(data, max_table_rows)
    title = question.strip().rstrip("?")[:70]
    out.widget = Widget(id=slugify("q_" + title), title=title[:1].upper() + title[1:], subtitle=plan.assumptions[:140],
                        steps=plan.steps, chart=plan.chart if plan.chart.type != "kpi" or len(data) == 1 else ChartConfig(type="table"))
    out.widget.layout.w = 3 if out.widget.chart.type == "kpi" else 6
    cols = ", ".join(map(str, data.columns[:12]))
    out.summary = f"{len(data)} rows; columns: {cols}. Answer: {out.answer[:300]}"
    return out
