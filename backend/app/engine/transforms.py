"""Whitelisted transformation operations implemented in pandas.

Each ``Step`` from the spec maps to exactly one function here. Nothing in this
module executes model-written code: the LLM only chooses operations and columns.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import numpy as np
import pandas as pd

from ..spec.models import Condition, Step
from .expressions import ExpressionError, evaluate_expression

MAX_PIVOT_COLUMNS = 60


class TransformError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Column resolution and value coercion
# --------------------------------------------------------------------------- #
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def resolve_column(df: pd.DataFrame, name: str, what: str = "column") -> str:
    if name in df.columns:
        return name
    key = str(name).strip().lower()
    for c in df.columns:
        if str(c).strip().lower() == key:
            return c
    nkey = _norm(name)
    for c in df.columns:
        if _norm(c) == nkey:
            return c
    raise TransformError(f"{what} '{name}' not found. Available columns: {', '.join(map(str, df.columns))}")


def resolve_columns(df: pd.DataFrame, names: list[str], what: str = "column") -> list[str]:
    out: list[str] = []
    for n in names:
        c = resolve_column(df, n, what)
        if c not in out:
            out.append(c)
    return out


def is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def is_datetime(s: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(s)


def _to_number(value: str) -> float:
    cleaned = re.sub(r"[,\s$€£¥%]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        raise TransformError(f"'{value}' is not a number") from None


def _coerce_value(series: pd.Series, value: str):
    if is_numeric(series):
        return _to_number(value)
    if is_datetime(series):
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            raise TransformError(f"'{value}' is not a date")
        return ts
    if pd.api.types.is_bool_dtype(series):
        return str(value).strip().lower() in {"true", "t", "yes", "y", "1"}
    return str(value).strip()


# --------------------------------------------------------------------------- #
# filter
# --------------------------------------------------------------------------- #
def _condition_mask(df: pd.DataFrame, cond: Condition) -> pd.Series:
    col = resolve_column(df, cond.column)
    s = df[col]
    op = cond.operator
    if op == "is_null":
        return s.isna()
    if op == "not_null":
        return s.notna()
    if op in ("in", "not_in"):
        vals = cond.values or ([cond.value] if cond.value else [])
        if not vals:
            raise TransformError(f"filter on '{col}' with '{op}' needs 'values'")
        if is_numeric(s) or is_datetime(s):
            typed = [_coerce_value(s, v) for v in vals]
            mask = s.isin(typed)
        else:
            lowered = s.astype("string").str.strip().str.lower()
            mask = lowered.isin([str(v).strip().lower() for v in vals])
        return mask if op == "in" else ~mask.fillna(False)
    if op == "between":
        vals = cond.values if len(cond.values) >= 2 else []
        if not vals:
            raise TransformError(f"filter on '{col}' with 'between' needs two 'values'")
        lo, hi = _coerce_value(s, vals[0]), _coerce_value(s, vals[1])
        if is_datetime(s) and re.fullmatch(r"\d{4}(-\d{2})?", str(vals[1]).strip()):
            hi = _period_end(str(vals[1]).strip())
        return (s >= lo) & (s <= hi)
    value = cond.value
    if op in ("contains", "not_contains", "starts_with", "ends_with"):
        text = s.astype("string").str.lower()
        v = str(value).lower()
        if op == "contains":
            return text.str.contains(re.escape(v), regex=True, na=False)
        if op == "not_contains":
            return ~text.str.contains(re.escape(v), regex=True, na=False)
        if op == "starts_with":
            return text.str.startswith(v, na=False)
        return text.str.endswith(v, na=False)
    # eq / ne / gt / gte / lt / lte
    if is_datetime(s) and re.fullmatch(r"\d{4}(-\d{2})?", str(value).strip()):
        # Year or year-month given: compare on the period instead of an exact timestamp.
        period = str(value).strip()
        start, end = pd.Timestamp(_period_start(period)), _period_end(period)
        if op == "eq":
            return (s >= start) & (s <= end)
        if op == "ne":
            return ~((s >= start) & (s <= end))
        if op in ("gt",):
            return s > end
        if op == "gte":
            return s >= start
        if op == "lt":
            return s < start
        return s <= end
    typed = _coerce_value(s, value)
    if is_numeric(s) or is_datetime(s) or pd.api.types.is_bool_dtype(s):
        left = s
    else:
        left = s.astype("string").str.strip().str.lower()
        typed = str(typed).lower()
    ops: dict[str, Callable] = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "gt": lambda a, b: a > b,
                                "gte": lambda a, b: a >= b, "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b}
    return ops[op](left, typed)


def _period_start(p: str) -> str:
    return f"{p}-01-01" if len(p) == 4 else f"{p}-01"


def _period_end(p: str) -> pd.Timestamp:
    if len(p) == 4:
        return pd.Timestamp(f"{p}-12-31 23:59:59")
    return pd.Period(p, freq="M").end_time.floor("s")


def apply_filter(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    masks = [_condition_mask(df, c).fillna(False).astype(bool) for c in step.conditions]
    mask = masks[0]
    for m in masks[1:]:
        mask = (mask & m) if step.logic == "and" else (mask | m)
    return df[mask].reset_index(drop=True)


def apply_conditions(df: pd.DataFrame, conditions: list[Condition]) -> pd.DataFrame:
    if not conditions:
        return df
    return apply_filter(df, Step(op="filter", conditions=conditions, logic="and"))


# --------------------------------------------------------------------------- #
# column-producing steps
# --------------------------------------------------------------------------- #
def apply_derive(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    out = df.copy()
    try:
        out[step.name] = evaluate_expression(step.expression, df)
    except ExpressionError as exc:
        raise TransformError(str(exc)) from exc
    return out


def _as_datetime(df: pd.DataFrame, col: str) -> pd.Series:
    s = df[col]
    if is_datetime(s):
        return s
    parsed = pd.to_datetime(s, errors="coerce")
    if parsed.notna().sum() == 0 and s.notna().sum() > 0:
        raise TransformError(f"column '{col}' does not contain dates")
    return parsed


def apply_date_bucket(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column)
    d = _as_datetime(df, col)
    name = step.name or f"{col}_{step.unit}"
    out = df.copy()
    unit = step.unit
    if unit == "year":
        out[name] = d.dt.strftime("%Y")
    elif unit == "quarter":
        q = d.dt.year.astype("Int64").astype("string") + "-Q" + d.dt.quarter.astype("Int64").astype("string")
        out[name] = q.where(d.notna(), pd.NA)
    elif unit == "month":
        out[name] = d.dt.to_period("M").dt.to_timestamp()
    elif unit == "week":
        out[name] = d.dt.to_period("W-SUN").dt.start_time
    else:
        out[name] = d.dt.normalize()
    return out


def apply_date_part(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column)
    d = _as_datetime(df, col)
    name = step.name or f"{col}_{step.unit}"
    out = df.copy()
    unit = step.unit
    if unit == "year":
        out[name] = d.dt.year.astype("Int64")
    elif unit == "quarter":
        out[name] = d.dt.quarter.astype("Int64")
    elif unit == "month":
        out[name] = d.dt.month.astype("Int64")
    elif unit == "month_name":
        out[name] = d.dt.strftime("%b")
    elif unit == "week":
        out[name] = d.dt.isocalendar().week.astype("Int64")
    elif unit == "weekday":
        out[name] = d.dt.strftime("%a")
    elif unit == "day":
        out[name] = d.dt.day.astype("Int64")
    else:
        out[name] = d.dt.hour.astype("Int64")
    return out


# --------------------------------------------------------------------------- #
# aggregate / pivot / unpivot
# --------------------------------------------------------------------------- #
_AGG = {"sum": "sum", "mean": "mean", "median": "median", "min": "min", "max": "max", "count": "count",
        "count_distinct": "nunique", "first": "first", "last": "last"}


def apply_aggregate(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    keys = resolve_columns(df, step.group_by, "group_by column")
    named: dict[str, tuple[str, str]] = {}
    row_count_name: str | None = None
    for m in step.metrics:
        out_name = m.output_name
        if not m.column:
            if m.fn != "count":
                raise TransformError(f"metric '{out_name}': an empty column is only valid with fn=count (row count)")
            row_count_name = out_name
            continue
        col = resolve_column(df, m.column, "metric column")
        if m.fn in ("sum", "mean", "median") and not is_numeric(df[col]):
            raise TransformError(f"metric '{out_name}': column '{col}' is not numeric, so {m.fn} is not possible "
                                 "(use count or count_distinct, or cast it to a number first)")
        named[out_name] = (col, _AGG[m.fn])
    if not keys:
        data: dict[str, list] = {}
        for out_name, (col, fn) in named.items():
            value = getattr(df[col], fn)()
            if fn == "sum" and df[col].count() == 0:
                value = np.nan
            data[out_name] = [value]
        if row_count_name:
            data[row_count_name] = [len(df)]
        return pd.DataFrame(data)
    grouped = df.groupby(keys, dropna=False, sort=True, observed=True)
    if named:
        result = grouped.agg(**{k: pd.NamedAgg(column=v[0], aggfunc=v[1]) for k, v in named.items()})
        for out_name, (col, fn) in named.items():
            if fn == "sum":  # pandas sums an all-NaN group to 0; keep it blank instead
                counts = grouped[col].count()
                result.loc[counts == 0, out_name] = np.nan
    else:
        result = grouped.size().to_frame(row_count_name or "row_count")
        if row_count_name:
            row_count_name = None
    if row_count_name:
        result[row_count_name] = grouped.size()
    result = result.reset_index()
    for k in keys:  # groupby turns string NA into NaN; keep labels readable
        if not is_numeric(result[k]) and not is_datetime(result[k]):
            result[k] = result[k].astype("string").fillna("(blank)")
    return result


def apply_pivot(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    if not step.group_by:
        raise TransformError("pivot needs 'group_by' (the row index columns)")
    index = resolve_columns(df, step.group_by, "group_by column")
    col = resolve_column(df, step.column)
    val = resolve_column(df, step.value, "value column")
    if df[col].nunique() > MAX_PIVOT_COLUMNS:
        raise TransformError(f"pivot: column '{col}' has {df[col].nunique()} distinct values (max {MAX_PIVOT_COLUMNS})")
    table = pd.pivot_table(df, index=index, columns=col, values=val, aggfunc=_AGG[step.fn], observed=True)
    table.columns = [str(c) for c in table.columns]
    return table.reset_index()


def apply_unpivot(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    ids = resolve_columns(df, step.group_by, "group_by column")
    vals = resolve_columns(df, step.columns)
    return df.melt(id_vars=ids, value_vars=vals, var_name=step.name or "variable", value_name=step.value or "value")


# --------------------------------------------------------------------------- #
# shaping steps
# --------------------------------------------------------------------------- #
def apply_sort(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    cols = resolve_columns(df, step.columns, "sort column")
    return df.sort_values(cols, ascending=not step.descending, kind="mergesort", na_position="last").reset_index(drop=True)


def apply_limit(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    return df.head(step.n).reset_index(drop=True)


def apply_top_n(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column, "metric column")
    if not is_numeric(df[col]):
        raise TransformError(f"top_n: column '{col}' is not numeric")
    ordered = df.sort_values(col, ascending=not step.descending, kind="mergesort", na_position="last")
    top = ordered.head(step.n)
    rest = ordered.iloc[step.n:]
    if step.value and len(rest):
        other = {}
        for c in df.columns:
            if is_numeric(df[c]):
                other[c] = rest[c].sum()
            elif is_datetime(df[c]):
                other[c] = pd.NaT
            else:
                other[c] = step.value
        top = pd.concat([top, pd.DataFrame([other])], ignore_index=True)
    return top.reset_index(drop=True)


def apply_rename(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    mapping = {resolve_column(df, r.from_): r.to for r in step.renames if r.from_ and r.to}
    return df.rename(columns=mapping)


def apply_select(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    return df[resolve_columns(df, step.columns)]


def apply_cast(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    from .loader import coerce_series

    col = resolve_column(df, step.column)
    out = df.copy()
    out[col], _ = coerce_series(df[col], {"number": "number", "text": "text", "date": "date", "boolean": "boolean"}[step.unit])
    return out


def apply_drop_nulls(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    cols = resolve_columns(df, step.columns) if step.columns else None
    return df.dropna(subset=cols, how="any").reset_index(drop=True)


def apply_fill_nulls(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column)
    out = df.copy()
    s = out[col]
    value = _coerce_value(s, step.value) if (is_numeric(s) or is_datetime(s)) and step.value != "" else step.value
    if is_numeric(s) and step.value == "":
        value = 0
    out[col] = s.fillna(value)
    return out


def apply_bin(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column)
    if not is_numeric(df[col]):
        raise TransformError(f"bin: column '{col}' is not numeric")
    out = df.copy()
    name = step.name or f"{col}_bin"
    bins = max(2, min(50, step.n or 10))
    cats = pd.cut(df[col].astype("float64"), bins=bins, include_lowest=True)
    out[name] = cats.apply(lambda iv: f"{iv.left:,.4g} – {iv.right:,.4g}" if pd.notna(iv) else None).astype("string")
    out["__bin_order"] = cats.cat.codes
    out = out.sort_values("__bin_order", kind="mergesort").drop(columns="__bin_order").reset_index(drop=True)
    return out


def _partition(df: pd.DataFrame, step: Step) -> list[str]:
    return resolve_columns(df, step.group_by, "group_by column") if step.group_by else []


def apply_pct_of_total(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column)
    if not is_numeric(df[col]):
        raise TransformError(f"pct_of_total: column '{col}' is not numeric")
    out = df.copy()
    name = step.name or f"{col}_pct"
    keys = _partition(df, step)
    total = out.groupby(keys, dropna=False)[col].transform("sum") if keys else out[col].sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        out[name] = (100.0 * out[col] / total).replace([np.inf, -np.inf], np.nan)
    return out


def apply_cumulative(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column)
    if not is_numeric(df[col]):
        raise TransformError(f"cumulative: column '{col}' is not numeric")
    out = df.copy()
    name = step.name or f"{col}_cumulative"
    keys = _partition(df, step)
    out[name] = out.groupby(keys, dropna=False)[col].cumsum() if keys else out[col].cumsum()
    return out


def apply_period_change(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column)
    if not is_numeric(df[col]):
        raise TransformError(f"period_change: column '{col}' is not numeric")
    out = df.copy()
    name = step.name or f"{col}_change" + ("_pct" if step.kind == "pct" else "")
    keys = _partition(df, step)
    series = out.groupby(keys, dropna=False)[col] if keys else out[col]
    if step.kind == "pct":
        with np.errstate(divide="ignore", invalid="ignore"):
            out[name] = (series.pct_change(periods=step.n, fill_method=None) * 100.0).replace([np.inf, -np.inf], np.nan)
    else:
        out[name] = series.diff(periods=step.n)
    return out


def apply_rank(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    col = resolve_column(df, step.column)
    out = df.copy()
    name = step.name or f"{col}_rank"
    keys = _partition(df, step)
    series = out.groupby(keys, dropna=False)[col] if keys else out[col]
    out[name] = series.rank(method="min", ascending=not step.descending).astype("Int64")
    return out


OPS: dict[str, Callable[[pd.DataFrame, Step], pd.DataFrame]] = {
    "filter": apply_filter, "derive": apply_derive, "date_bucket": apply_date_bucket, "date_part": apply_date_part,
    "aggregate": apply_aggregate, "sort": apply_sort, "limit": apply_limit, "top_n": apply_top_n,
    "pivot": apply_pivot, "unpivot": apply_unpivot, "rename": apply_rename, "select": apply_select,
    "cast": apply_cast, "drop_nulls": apply_drop_nulls, "fill_nulls": apply_fill_nulls, "bin": apply_bin,
    "pct_of_total": apply_pct_of_total, "cumulative": apply_cumulative, "period_change": apply_period_change,
    "rank": apply_rank,
}


def apply_step(df: pd.DataFrame, step: Step) -> pd.DataFrame:
    fn = OPS.get(step.op)
    if fn is None:
        raise TransformError(f"unknown operation '{step.op}'")
    return fn(df, step)
