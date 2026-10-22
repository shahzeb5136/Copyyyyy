"""Build dashboard data from a spec + DataFrame. Deterministic: same file + same spec -> same output."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from ..spec.models import DashboardSpec, Widget
from .charts import ChartError, build_figure, render_with_fallback
from .pipeline import QueryError, run_steps
from .profile import frame_to_records


class WidgetResult(BaseModel):
    id: str
    type: str
    title: str = ""
    subtitle: str = ""
    status: str = "ok"  # ok | error | empty | pending
    error: str = ""
    kind: str = ""  # plotly | kpi | table | text
    figure: dict | None = None
    kpi: dict | None = None
    table: dict | None = None
    text: str = ""
    stale: bool = False
    note: str = ""
    row_count: int = 0
    w: int = 6


class DashboardResult(BaseModel):
    title: str = ""
    description: str = ""
    widgets: list[WidgetResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    built_at: str = ""
    source_rows: int = 0


def run_widget_query(df: pd.DataFrame, widget: Widget, spec: DashboardSpec) -> pd.DataFrame:
    return run_steps(df, widget.steps, spec.global_filters).df


def build_widget(df: pd.DataFrame, widget: Widget, spec: DashboardSpec, max_chart_rows: int = 5_000,
                 max_table_rows: int = 500, strict: bool = False) -> tuple[WidgetResult, pd.DataFrame | None]:
    """Render one widget. With ``strict=True`` chart problems raise instead of falling back (used while the
    LLM is still iterating on a draft, so it gets to fix its own mistakes)."""
    res = WidgetResult(id=widget.id, type=widget.type, title=widget.title, subtitle=widget.subtitle,
                       w=widget.layout.w)
    if widget.type == "text":
        res.kind = "text"
        res.text = widget.text.content
        res.stale = widget.text.stale
        if widget.text.mode == "generated" and not widget.text.content:
            res.status = "pending"
            res.note = "Summary not generated yet."
        return res, None
    currency = spec.semantic_model.currency
    try:
        data = run_widget_query(df, widget, spec)
    except QueryError as exc:
        res.status, res.error = "error", str(exc)
        return res, None
    res.row_count = int(len(data))
    if data.empty:
        res.status, res.error = "empty", "The query returned no rows."
        return res, data
    if len(data) > max_chart_rows and widget.chart.type not in ("table", "histogram", "scatter", "box"):
        res.note = f"Result had {len(data):,} rows; only the first {max_chart_rows:,} are drawn. Aggregate for a clearer chart."
        data = data.head(max_chart_rows)
    try:
        if strict:
            fig = build_figure(widget.chart, data, currency, max_table_rows)
        else:
            fig, _ = render_with_fallback(widget.chart, data, currency, max_table_rows)
    except ChartError as exc:
        res.status, res.error = "error", f"chart: {exc}"
        return res, data
    res.kind, res.figure, res.kpi, res.table = fig.kind, fig.figure, fig.kpi, fig.table
    if fig.kind == "plotly":
        res.table = frame_to_records(data, max_table_rows)  # "show data" / CSV download behind every chart
    if fig.note:
        res.note = (res.note + " " if res.note else "") + fig.note
    return res, data


def build_dashboard(df: pd.DataFrame, spec: DashboardSpec, max_chart_rows: int = 5_000, max_table_rows: int = 500,
                    strict: bool = False) -> DashboardResult:
    out = DashboardResult(title=spec.metadata.title, description=spec.metadata.description,
                          built_at=datetime.now(UTC).isoformat(timespec="seconds"), source_rows=int(len(df)))
    for w in spec.widgets:
        res, _ = build_widget(df, w, spec, max_chart_rows, max_table_rows, strict)
        out.widgets.append(res)
        if res.status == "error":
            out.warnings.append(f"'{w.title or w.id}': {res.error}")
    return out


def widget_data_for_llm(df: pd.DataFrame, widget: Widget, spec: DashboardSpec, max_rows: int = 60) -> str:
    """Compact CSV of a widget's computed data, for narrative generation. Never the raw file."""
    try:
        data = run_widget_query(df, widget, spec)
    except QueryError as exc:
        return f"(could not compute data: {exc})"
    if data.empty:
        return "(no rows)"
    rec = frame_to_records(data, max_rows)
    lines = [",".join(rec["columns"])]
    for row in rec["rows"]:
        lines.append(",".join("" if v is None else str(v) for v in row))
    if rec["truncated"]:
        lines.append(f"... {rec['row_count'] - max_rows} more rows not shown")
        num = [c for c in data.columns if pd.api.types.is_numeric_dtype(data[c])]
        if num:
            stats = data[num].agg(["sum", "mean", "min", "max"]).T.round(2)
            lines.append("statistics over all rows:")
            lines.append(stats.to_csv().strip())
    return "\n".join(lines)


def results_summary(result: DashboardResult) -> dict[str, Any]:
    return {"ok": sum(1 for w in result.widgets if w.status == "ok"),
            "errors": [w.id for w in result.widgets if w.status == "error"],
            "empty": [w.id for w in result.widgets if w.status == "empty"]}
