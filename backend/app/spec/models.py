"""Pydantic models for the dashboard spec (``spec_version`` 1.0).

Design rules
* The spec contains **no raw data** - only column names, a fingerprint, the semantic
  model, declarative transformation steps and chart definitions.
* Every transformation is one of a fixed set of whitelisted operations that the
  engine implements in pandas. The LLM never produces code.
* Models use plain defaults everywhere, so a saved spec can omit unused fields and
  the LLM-facing (strict) schema can be generated from the very same classes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SPEC_VERSION = "1.0"

DataType = Literal["number", "integer", "date", "datetime", "boolean", "text"]
Role = Literal["measure", "dimension", "time", "identifier", "attribute", "ignore"]
SemanticType = Literal[
    "currency", "percent", "count", "quantity", "ratio", "score", "category", "status", "id", "name", "text",
    "date", "year", "period", "boolean", "geo", "other",
]
AggFn = Literal["sum", "mean", "median", "min", "max", "count", "count_distinct", "first", "last"]
ChartType = Literal[
    "bar", "line", "area", "scatter", "pie", "histogram", "box", "heatmap", "treemap", "waterfall", "funnel",
    "gantt", "table", "kpi",
]
StepOp = Literal[
    "filter", "derive", "date_bucket", "date_part", "aggregate", "sort", "limit", "top_n", "pivot", "unpivot",
    "rename", "select", "cast", "drop_nulls", "fill_nulls", "bin", "pct_of_total", "cumulative", "period_change",
    "rank",
]
Operator = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "not_contains", "starts_with", "ends_with",
    "is_null", "not_null", "between",
]

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "item") -> str:
    s = _SLUG.sub("_", str(text).strip().lower()).strip("_")
    return s[:48] or fallback


class StrictBase(BaseModel):
    """Common config: ignore unknown keys from older/newer specs, strip whitespace."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# Semantic model
# --------------------------------------------------------------------------- #
class ColumnSemantics(StrictBase):
    name: str = Field(description="Exact column name as it appears in the file.")
    description: str = Field("", description="Plain-English meaning of the column (units, codes, caveats).")
    data_type: DataType = "text"
    role: Role = Field("attribute", description="measure = numeric to aggregate; dimension = category to group by; "
                                                "time = date to trend over; identifier = unique key; "
                                                "attribute = descriptive text; ignore = not useful.")
    semantic_type: SemanticType = "other"
    unit: str = Field("", description="Unit or currency code, e.g. 'USD', '%', 'hours', 'units'.")
    aggregation: AggFn = Field("sum", description="Default aggregation when the column is used as a measure.")


class SemanticModel(StrictBase):
    title: str = "Untitled dataset"
    description: str = ""
    domain: str = Field("", description="Business domain, e.g. finance, hr, sales, projects, operations.")
    grain: str = Field("", description="What one row represents, e.g. 'one row per order line'.")
    data_shape: Literal["row_level", "aggregated", "unclear"] = "unclear"
    data_shape_reasoning: str = ""
    time_column: str = Field("", description="Primary date column for trends, or empty.")
    currency: str = Field("", description="Currency code for money columns, or empty.")
    columns: list[ColumnSemantics] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    open_questions: list[str] = Field(default_factory=list,
                                      description="Questions for the user that would improve the dashboard.")
    business_notes: list[str] = Field(default_factory=list,
                                      description="Clarifications and definitions confirmed by the user.")

    def column(self, name: str) -> ColumnSemantics | None:
        return next((c for c in self.columns if c.name == name), None)

    def measures(self) -> list[ColumnSemantics]:
        return [c for c in self.columns if c.role == "measure"]

    def dimensions(self) -> list[ColumnSemantics]:
        return [c for c in self.columns if c.role == "dimension"]

    def time_columns(self) -> list[ColumnSemantics]:
        return [c for c in self.columns if c.role == "time" or c.data_type in ("date", "datetime")]


class SemanticRefinement(StrictBase):
    """LLM output for the semantic-model chat loop."""

    reply: str = Field("", description="Short reply to the user explaining what changed or asking a question.")
    model: SemanticModel = Field(default_factory=SemanticModel)


# --------------------------------------------------------------------------- #
# Transformations
# --------------------------------------------------------------------------- #
class Condition(StrictBase):
    column: str
    operator: Operator = "eq"
    value: str = Field("", description="Comparison value as text (numbers and dates are parsed by the engine).")
    values: list[str] = Field(default_factory=list, description="Values for 'in', 'not_in' and 'between'.")


class Metric(StrictBase):
    column: str = Field("", description="Input column. Leave empty with fn=count to count rows.")
    fn: AggFn = "sum"
    name: str = Field("", description="Output column name (default: <fn>_<column>).")

    @property
    def output_name(self) -> str:
        if self.name:
            return self.name
        return f"{self.fn}_{self.column}" if self.column else "row_count"


class Rename(StrictBase):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, populate_by_name=True)

    from_: str = Field("", alias="from")
    to: str = ""


class Step(StrictBase):
    """One declarative transformation. Which fields are used depends on ``op``:

    filter        conditions, logic
    derive        name, expression            (arithmetic over columns, e.g. "actual - budget")
    date_bucket   column, unit, name          (unit: year | quarter | month | week | day)
    date_part     column, unit, name          (unit: year | quarter | month | month_name | week | weekday | day | hour)
    aggregate     group_by, metrics
    sort          columns, descending
    limit         n
    top_n         column (metric), n, descending, value (label for the remainder, e.g. "Other"; empty = drop)
    pivot         group_by (index), column (becomes new columns), value (value column), fn
    unpivot       group_by (id columns kept), columns (columns to unpivot), name (variable col), value (value col)
    rename        renames
    select        columns
    cast          column, unit (target: number | text | date | boolean)
    drop_nulls    columns (empty = any column)
    fill_nulls    column, value
    bin           column, n (number of bins), name
    pct_of_total  column, name, group_by (partition)
    cumulative    column, name, group_by (partition)
    period_change column, name, n (periods back), kind (abs | pct), group_by (partition)
    rank          column, name, descending, group_by (partition)
    """

    op: StepOp
    column: str = ""
    columns: list[str] = Field(default_factory=list)
    name: str = ""
    expression: str = ""
    conditions: list[Condition] = Field(default_factory=list)
    logic: Literal["and", "or"] = "and"
    group_by: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    unit: str = ""
    descending: bool = True
    n: int = 0
    value: str = ""
    fn: AggFn = "sum"
    kind: Literal["abs", "pct"] = "abs"
    renames: list[Rename] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Step:
        op = self.op
        need = {
            "filter": bool(self.conditions),
            "derive": bool(self.name and self.expression),
            "date_bucket": bool(self.column) and self.unit in {"year", "quarter", "month", "week", "day"},
            "date_part": bool(self.column) and self.unit in {"year", "quarter", "month", "month_name", "week",
                                                             "weekday", "day", "hour"},
            "aggregate": bool(self.metrics),
            "sort": bool(self.columns),
            "limit": self.n > 0,
            "top_n": bool(self.column) and self.n > 0,
            "pivot": bool(self.column and self.value),
            "unpivot": bool(self.columns),
            "rename": bool(self.renames),
            "select": bool(self.columns),
            "cast": bool(self.column) and self.unit in {"number", "text", "date", "boolean"},
            "drop_nulls": True,
            "fill_nulls": bool(self.column),
            "bin": bool(self.column),
            "pct_of_total": bool(self.column),
            "cumulative": bool(self.column),
            "period_change": bool(self.column),
            "rank": bool(self.column),
        }
        hints = {
            "filter": "needs at least one condition",
            "derive": "needs 'name' and 'expression'",
            "date_bucket": "needs 'column' and unit in year|quarter|month|week|day",
            "date_part": "needs 'column' and unit in year|quarter|month|month_name|week|weekday|day|hour",
            "aggregate": "needs at least one metric",
            "sort": "needs 'columns'",
            "limit": "needs n > 0",
            "top_n": "needs 'column' (the metric to rank by) and n > 0",
            "pivot": "needs 'column' (values become new columns) and 'value' (value column)",
            "unpivot": "needs 'columns' (the columns to unpivot)",
            "rename": "needs 'renames'",
            "select": "needs 'columns'",
            "cast": "needs 'column' and unit in number|text|date|boolean",
            "fill_nulls": "needs 'column'",
            "bin": "needs 'column'",
            "pct_of_total": "needs 'column'",
            "cumulative": "needs 'column'",
            "period_change": "needs 'column'",
            "rank": "needs 'column'",
        }
        if not need.get(op, True):
            raise ValueError(f"step '{op}' {hints.get(op, 'is incomplete')}")
        if op == "bin" and self.n <= 0:
            self.n = 10
        if op == "period_change" and self.n <= 0:
            self.n = 1
        return self


# --------------------------------------------------------------------------- #
# Charts and widgets
# --------------------------------------------------------------------------- #
class ChartConfig(StrictBase):
    """How to draw the query result. Column names refer to the *output* of the steps."""

    type: ChartType = "bar"
    x: str = Field("", description="Category / time column (bar, line, area, scatter, pie labels, heatmap x).")
    y: list[str] = Field(default_factory=list, description="Numeric value column(s). kpi: the single value column.")
    color: str = Field("", description="Column that splits series (optional).")
    size: str = Field("", description="scatter: numeric column for marker size (optional).")
    z: str = Field("", description="heatmap: value column.")
    path: list[str] = Field(default_factory=list, description="treemap: hierarchy columns outer to inner.")
    label: str = Field("", description="gantt: row label column.")
    start: str = Field("", description="gantt: start date column.")
    end: str = Field("", description="gantt: end date column.")
    orientation: Literal["v", "h"] = "v"
    barmode: Literal["group", "stack"] = "group"
    sort: Literal["none", "asc", "desc"] = "none"
    value_format: Literal["number", "currency", "percent"] = "number"
    show_values: bool = Field(False, description="Draw value labels on the marks.")
    x_title: str = ""
    y_title: str = ""


class TextConfig(StrictBase):
    content: str = Field("", description="Markdown text. For mode=generated, leave empty: the app writes it.")
    mode: Literal["static", "generated"] = "static"
    source_widget_id: str = Field("", description="generated: id of the chart widget to summarise.")
    generated_at: str = ""
    stale: bool = Field(False, description="True when the data changed after the text was generated.")


class Layout(StrictBase):
    w: int = Field(6, description="Width in grid columns out of 12 (3 = KPI tile, 6 = half, 12 = full width).")

    @field_validator("w")
    @classmethod
    def _clamp(cls, v: int) -> int:
        return min(12, max(2, int(v or 6)))


class Widget(StrictBase):
    id: str = ""
    type: Literal["chart", "text"] = "chart"
    title: str = ""
    subtitle: str = Field("", description="One-line caption shown under the title (optional).")
    steps: list[Step] = Field(default_factory=list, description="chart: transformations producing the chart data.")
    chart: ChartConfig = Field(default_factory=ChartConfig)
    text: TextConfig = Field(default_factory=TextConfig)
    layout: Layout = Field(default_factory=Layout)

    @model_validator(mode="after")
    def _defaults(self) -> Widget:
        if not self.id:
            self.id = slugify(self.title, "widget")
        if self.type == "chart" and self.chart.type == "kpi" and self.layout.w == 6:
            self.layout.w = 3
        return self


class DashboardDraft(StrictBase):
    """LLM output when designing a dashboard (no fingerprint/metadata - the app adds those)."""

    title: str = ""
    description: str = ""
    global_filters: list[Condition] = Field(default_factory=list,
                                            description="Filters applied to every widget (usually empty).")
    widgets: list[Widget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> DashboardDraft:
        seen: dict[str, int] = {}
        for w in self.widgets:
            base = w.id or "widget"
            if base in seen:
                seen[base] += 1
                w.id = f"{base}_{seen[base]}"
            else:
                seen[base] = 1
        return self


class DashboardRefinement(StrictBase):
    reply: str = Field("", description="Short reply to the user describing the change made.")
    draft: DashboardDraft = Field(default_factory=DashboardDraft)


# --------------------------------------------------------------------------- #
# The saved spec
# --------------------------------------------------------------------------- #
class FingerprintColumn(StrictBase):
    name: str
    data_type: DataType = "text"


class SourceFingerprint(StrictBase):
    file_name: str = ""
    sheet: str = ""
    row_count: int = 0
    columns: list[FingerprintColumn] = Field(default_factory=list)


class Metadata(StrictBase):
    title: str = "Dashboard"
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    app_version: str = ""
    llm_provider: str = ""
    llm_model: str = ""
    created_by: str = ""


class DashboardSpec(StrictBase):
    spec_version: str = SPEC_VERSION
    metadata: Metadata = Field(default_factory=Metadata)
    source_fingerprint: SourceFingerprint = Field(default_factory=SourceFingerprint)
    semantic_model: SemanticModel = Field(default_factory=SemanticModel)
    global_filters: list[Condition] = Field(default_factory=list)
    widgets: list[Widget] = Field(default_factory=list)

    def widget(self, widget_id: str) -> Widget | None:
        return next((w for w in self.widgets if w.id == widget_id), None)

    def to_draft(self) -> DashboardDraft:
        return DashboardDraft(title=self.metadata.title, description=self.metadata.description,
                              global_filters=list(self.global_filters), widgets=list(self.widgets))

    def to_json_dict(self) -> dict[str, Any]:
        """Compact JSON (defaults omitted) - this is what the user downloads."""
        data = self.model_dump(mode="json", exclude_defaults=True, by_alias=True)
        data["spec_version"] = self.spec_version
        return data


# --------------------------------------------------------------------------- #
# Ask-a-question output
# --------------------------------------------------------------------------- #
class AskPlan(StrictBase):
    can_answer: bool = True
    reasoning: str = Field("", description="One or two sentences on how the steps answer the question.")
    assumptions: str = Field("", description="Interpretations made, or why the question can't be answered.")
    steps: list[Step] = Field(default_factory=list)
    chart: ChartConfig = Field(default_factory=ChartConfig, description="Chart for the result; type=table if none.")
