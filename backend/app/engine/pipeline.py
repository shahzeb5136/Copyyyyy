"""Run a widget's steps against a DataFrame, with clear per-step error messages."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..spec.models import Condition, Step
from .expressions import ExpressionError, referenced_columns
from .transforms import TransformError, apply_conditions, apply_step


class QueryError(ValueError):
    def __init__(self, message: str, step_index: int | None = None):
        super().__init__(message)
        self.step_index = step_index


@dataclass
class QueryResult:
    df: pd.DataFrame
    warnings: list[str] = field(default_factory=list)


def run_steps(df: pd.DataFrame, steps: list[Step], global_filters: list[Condition] | None = None) -> QueryResult:
    out = df
    try:
        out = apply_conditions(out, global_filters or [])
    except TransformError as exc:
        raise QueryError(f"global filter: {exc}") from exc
    for i, step in enumerate(steps):
        try:
            out = apply_step(out, step)
        except TransformError as exc:
            raise QueryError(f"step {i + 1} ({step.op}): {exc}", i) from exc
        except (KeyError, ValueError, TypeError) as exc:
            raise QueryError(f"step {i + 1} ({step.op}): {type(exc).__name__}: {str(exc)[:300]}", i) from exc
        if out is None:
            raise QueryError(f"step {i + 1} ({step.op}) produced no result", i)
    return QueryResult(df=out.reset_index(drop=True))


def columns_referenced(steps: list[Step], conditions: list[Condition] | None = None) -> set[str]:
    """Source columns a list of steps reads. Columns created by earlier steps are excluded."""
    created: set[str] = set()
    needed: set[str] = set()

    def need(name: str) -> None:
        if name and name not in created:
            needed.add(name)

    for c in conditions or []:
        need(c.column)
    for s in steps:
        op = s.op
        if op == "filter":
            for c in s.conditions:
                need(c.column)
        elif op == "derive":
            try:
                for c in referenced_columns(s.expression):
                    need(c)
            except ExpressionError:
                pass
            created.add(s.name)
        elif op in ("date_bucket", "date_part"):
            need(s.column)
            created.add(s.name or f"{s.column}_{s.unit}")
        elif op == "aggregate":
            for g in s.group_by:
                need(g)
            for m in s.metrics:
                need(m.column)
            created |= {m.output_name for m in s.metrics}
        elif op == "sort":
            for c in s.columns:
                need(c)
        elif op == "top_n":
            need(s.column)
        elif op == "pivot":
            for g in s.group_by:
                need(g)
            need(s.column)
            need(s.value)
        elif op == "unpivot":
            for g in s.group_by:
                need(g)
            for c in s.columns:
                need(c)
            created |= {s.name or "variable", s.value or "value"}
        elif op == "rename":
            for r in s.renames:
                need(r.from_)
                created.add(r.to)
        elif op == "select":
            for c in s.columns:
                need(c)
        elif op in ("cast", "fill_nulls"):
            need(s.column)
        elif op == "drop_nulls":
            for c in s.columns:
                need(c)
        elif op in ("bin", "pct_of_total", "cumulative", "period_change", "rank"):
            need(s.column)
            for g in s.group_by:
                need(g)
            created.add(s.name)
    return {n for n in needed if n}
