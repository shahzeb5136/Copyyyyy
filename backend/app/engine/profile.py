"""Column profiling: compact, data-safe facts about a table.

The profile is what the LLM sees - never the full data. For text columns it gets
at most ``max_distinct`` top values; for numeric columns summary statistics and
percentiles; for every column a handful of sample values.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from ..spec.models import FingerprintColumn, SourceFingerprint


class NumericStats(BaseModel):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    std: float | None = None
    p5: float | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    p95: float | None = None
    sum: float | None = None
    zeros: int = 0
    negatives: int = 0
    is_integer: bool = False


class DateStats(BaseModel):
    min: str = ""
    max: str = ""
    span_days: int = 0
    granularity: str = ""  # day | month | quarter | year | datetime | mixed
    distinct: int = 0


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    position: int
    data_type: str
    non_null: int
    nulls: int
    null_pct: float
    distinct: int
    unique_ratio: float
    samples: list[str] = Field(default_factory=list)
    numeric: NumericStats | None = None
    dates: DateStats | None = None
    top_values: list[ValueCount] = Field(default_factory=list)
    values_truncated: bool = False
    avg_length: float = 0.0
    coercion_failures: int = 0


class TableProfile(BaseModel):
    file_name: str = ""
    sheet: str = ""
    row_count: int = 0
    column_count: int = 0
    duplicate_rows: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)

    def fingerprint(self) -> SourceFingerprint:
        return SourceFingerprint(file_name=self.file_name, sheet=self.sheet, row_count=self.row_count,
                                 columns=[FingerprintColumn(name=c.name, data_type=c.data_type) for c in self.columns])


# --------------------------------------------------------------------------- #
def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, 6)


def format_cell(v) -> Any:
    """JSON-friendly rendering of a cell (used for sample rows and table widgets)."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d") if v == v.normalize() else v.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 6)
    return v


def _date_granularity(s: pd.Series) -> str:
    d = s.dropna()
    if d.empty:
        return ""
    if (d.dt.normalize() != d).any():
        return "datetime"
    if (d.dt.day == 1).all():
        months = d.dt.month
        if (months == 1).all():
            return "year"
        if months.isin([1, 4, 7, 10]).all() and d.dt.month.nunique() <= 4:
            return "quarter"
        return "month"
    return "day"


def profile_column(name: str, position: int, s: pd.Series, data_type: str, max_distinct: int = 100,
                   coercion_failures: int = 0) -> ColumnProfile:
    n = len(s)
    non_null = s.dropna()
    nulls = n - len(non_null)
    distinct = int(non_null.nunique())
    prof = ColumnProfile(
        name=name, position=position, data_type=data_type, non_null=len(non_null), nulls=nulls,
        null_pct=round(100.0 * nulls / n, 2) if n else 0.0, distinct=distinct,
        unique_ratio=round(distinct / len(non_null), 4) if len(non_null) else 0.0,
        coercion_failures=coercion_failures,
    )
    head = non_null.drop_duplicates().head(5)
    prof.samples = [str(format_cell(v)) for v in head]

    if data_type in ("integer", "number") and len(non_null):
        vals = pd.to_numeric(non_null, errors="coerce").astype("float64").dropna()
        if len(vals):
            q = vals.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
            prof.numeric = NumericStats(
                min=_f(vals.min()), max=_f(vals.max()), mean=_f(vals.mean()), std=_f(vals.std()) if len(vals) > 1 else 0.0,
                p5=_f(q.iloc[0]), p25=_f(q.iloc[1]), p50=_f(q.iloc[2]), p75=_f(q.iloc[3]), p95=_f(q.iloc[4]),
                sum=_f(vals.sum()), zeros=int((vals == 0).sum()), negatives=int((vals < 0).sum()),
                is_integer=bool((vals.round() == vals).all()),
            )
        # Low-cardinality numbers (years, codes) are also useful as values
        if distinct <= 25:
            counts = non_null.value_counts().head(max_distinct)
            prof.top_values = [ValueCount(value=str(format_cell(k)), count=int(v)) for k, v in counts.items()]
    elif data_type in ("date", "datetime") and len(non_null):
        d = pd.to_datetime(non_null, errors="coerce").dropna()
        if len(d):
            prof.dates = DateStats(min=str(format_cell(d.min())), max=str(format_cell(d.max())),
                                   span_days=int((d.max() - d.min()).days), granularity=_date_granularity(d),
                                   distinct=distinct)
    elif data_type == "boolean":
        counts = non_null.value_counts()
        prof.top_values = [ValueCount(value=str(k), count=int(v)) for k, v in counts.items()]
    else:
        text = non_null.astype("string")
        prof.avg_length = round(float(text.str.len().mean()), 1) if len(text) else 0.0
        counts = text.value_counts().head(max_distinct)
        prof.top_values = [ValueCount(value=str(k)[:120], count=int(v)) for k, v in counts.items()]
        prof.values_truncated = distinct > max_distinct
    return prof


def profile_table(df: pd.DataFrame, types: dict[str, str], file_name: str = "", sheet: str = "",
                  sample_rows: int = 5, max_distinct: int = 100, warnings: list[str] | None = None,
                  coercion_failures: dict[str, int] | None = None) -> TableProfile:
    cols = [profile_column(str(c), i, df[c], types.get(c, "text"), max_distinct, (coercion_failures or {}).get(c, 0))
            for i, c in enumerate(df.columns)]
    n = len(df)
    try:
        dupes = int(df.duplicated().sum()) if n <= 2_000_000 else 0
    except Exception:  # noqa: BLE001 - unhashable cells
        dupes = 0
    sample = _sample_rows(df, sample_rows)
    return TableProfile(file_name=file_name, sheet=sheet, row_count=n, column_count=len(df.columns),
                        duplicate_rows=dupes, columns=cols, sample_rows=sample, warnings=list(warnings or []))


def _sample_rows(df: pd.DataFrame, n: int) -> list[dict[str, Any]]:
    if n <= 0 or df.empty:
        return []
    if len(df) <= n:
        picked = df
    else:
        head = df.head(min(2, n))
        rest = df.iloc[len(head):]
        extra = rest.sample(n=min(n - len(head), len(rest)), random_state=0).sort_index() if n > len(head) else rest.head(0)
        picked = pd.concat([head, extra])
    return [{str(k): format_cell(v) for k, v in row.items()} for row in picked.to_dict(orient="records")]


def frame_to_records(df: pd.DataFrame, max_rows: int | None = None) -> dict[str, Any]:
    """{columns, rows, truncated} with JSON-safe values (used by table widgets and ask results)."""
    truncated = max_rows is not None and len(df) > max_rows
    view = df.head(max_rows) if truncated else df
    columns = [str(c) for c in view.columns]
    rows = [[format_cell(v) for v in rec] for rec in view.itertuples(index=False, name=None)]
    return {"columns": columns, "rows": rows, "truncated": truncated, "row_count": int(len(df))}
