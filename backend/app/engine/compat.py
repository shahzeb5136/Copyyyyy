"""Is this file compatible with that saved spec? Used by replay."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..spec.models import DashboardSpec
from .pipeline import columns_referenced
from .profile import TableProfile

_COMPATIBLE = {
    frozenset({"integer", "number"}),
    frozenset({"date", "datetime"}),
}


class TypeChange(BaseModel):
    column: str
    expected: str
    actual: str
    breaking: bool = False


class CompatibilityReport(BaseModel):
    ok: bool = True
    missing_required: list[str] = Field(default_factory=list)
    missing_other: list[str] = Field(default_factory=list)
    extra_columns: list[str] = Field(default_factory=list)
    type_changes: list[TypeChange] = Field(default_factory=list)
    affected_widgets: list[str] = Field(default_factory=list)
    row_count_before: int = 0
    row_count_after: int = 0
    messages: list[str] = Field(default_factory=list)


def _norm(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def required_columns(spec: DashboardSpec) -> dict[str, list[str]]:
    """column -> widget ids that read it (source columns only)."""
    needs: dict[str, list[str]] = {}
    for w in spec.widgets:
        if w.type != "chart":
            continue
        cols = columns_referenced(w.steps, spec.global_filters)
        if not w.steps:  # chart drawn straight from source columns
            cols |= {c for c in [w.chart.x, w.chart.color, w.chart.size, w.chart.z, w.chart.label, w.chart.start,
                                 w.chart.end, *w.chart.y, *w.chart.path] if c}
        for c in cols:
            needs.setdefault(c, []).append(w.id)
    return needs


def check_compatibility(spec: DashboardSpec, profile: TableProfile) -> CompatibilityReport:
    rep = CompatibilityReport(row_count_before=spec.source_fingerprint.row_count, row_count_after=profile.row_count)
    actual = {c.name: c.data_type for c in profile.columns}
    actual_norm = {_norm(k): k for k in actual}
    expected = {c.name: c.data_type for c in spec.source_fingerprint.columns}
    needs = required_columns(spec)

    def present(name: str) -> bool:
        return name in actual or _norm(name) in actual_norm

    for name, dtype in expected.items():
        if not present(name):
            (rep.missing_required if name in needs else rep.missing_other).append(name)
            continue
        real = actual.get(name) or actual[actual_norm[_norm(name)]]
        if real != dtype and frozenset({real, dtype}) not in _COMPATIBLE:
            breaking = name in needs and ({real, dtype} & {"text"} != set()) and dtype in ("integer", "number", "date", "datetime")
            rep.type_changes.append(TypeChange(column=name, expected=dtype, actual=real, breaking=breaking))
    for name in needs:
        if name not in expected and not present(name) and name not in rep.missing_required:
            rep.missing_required.append(name)
    rep.extra_columns = [c for c in actual if c not in expected and _norm(c) not in {_norm(e) for e in expected}]
    affected = set()
    for name in rep.missing_required:
        affected.update(needs.get(name, []))
    for tc in rep.type_changes:
        if tc.breaking:
            affected.update(needs.get(tc.column, []))
    rep.affected_widgets = sorted(affected)
    rep.ok = not rep.missing_required and not any(tc.breaking for tc in rep.type_changes)

    if rep.missing_required:
        rep.messages.append(f"Columns used by the dashboard are missing from the new file: {', '.join(rep.missing_required)}.")
    if rep.missing_other:
        rep.messages.append(f"Columns from the original file are missing (not used by any chart): {', '.join(rep.missing_other)}.")
    for tc in rep.type_changes:
        rep.messages.append(f"Column '{tc.column}' was {tc.expected} and is now {tc.actual}" + (" (this breaks charts using it)." if tc.breaking else "."))
    if rep.extra_columns:
        rep.messages.append(f"New columns not in the original file: {', '.join(rep.extra_columns[:15])}" + (" ..." if len(rep.extra_columns) > 15 else "") + ".")
    if rep.row_count_before and rep.row_count_after and (rep.row_count_after < 0.1 * rep.row_count_before or rep.row_count_after > 10 * rep.row_count_before):
        rep.messages.append(f"Row count changed a lot: {rep.row_count_before:,} -> {rep.row_count_after:,}.")
    return rep
