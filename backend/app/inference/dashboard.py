"""Dashboard design and refinement: LLM proposes a draft, the engine validates it against the real data."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd

from ..engine.dashboard import build_widget
from ..engine.profile import TableProfile
from ..llm_client import LLMClient, LLMError
from ..spec.models import (
    SPEC_VERSION,
    DashboardDraft,
    DashboardRefinement,
    DashboardSpec,
    Metadata,
    SemanticModel,
    Widget,
)
from . import prompts
from .fallback import default_dashboard
from .structured import call_structured

log = logging.getLogger("dashboard.inference")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def draft_to_spec(draft: DashboardDraft, profile: TableProfile, model: SemanticModel, base: DashboardSpec | None,
                  llm: LLMClient | None, app_version: str, created_by: str = "") -> DashboardSpec:
    now = _now()
    meta = base.metadata.model_copy() if base else Metadata(created_at=now, created_by=created_by)
    meta.title = draft.title or model.title or meta.title
    meta.description = draft.description or meta.description
    meta.updated_at = now
    meta.app_version = app_version
    if llm is not None:
        meta.llm_provider, meta.llm_model = llm.provider_name, llm.model
    elif not base:
        meta.llm_provider, meta.llm_model = "none", "heuristics"
    return DashboardSpec(spec_version=SPEC_VERSION, metadata=meta, source_fingerprint=profile.fingerprint(),
                         semantic_model=model, global_filters=draft.global_filters, widgets=draft.widgets)


def _widget_errors(df: pd.DataFrame, draft: DashboardDraft, model: SemanticModel) -> list[str]:
    """Execute every chart widget strictly; return one error per failing widget."""
    spec = DashboardSpec(semantic_model=model, global_filters=draft.global_filters, widgets=draft.widgets)
    errors: list[str] = []
    ids = {w.id for w in draft.widgets}
    for w in draft.widgets:
        if w.type == "text":
            if w.text.mode == "generated" and w.text.source_widget_id and w.text.source_widget_id not in ids:
                errors.append(f"widget '{w.id}': source_widget_id '{w.text.source_widget_id}' does not exist")
            continue
        res, data = build_widget(df, w, spec, strict=True)
        if res.status == "error":
            errors.append(f"widget '{w.id}': {res.error}")
        elif res.status == "empty":
            errors.append(f"widget '{w.id}': the steps return no rows (check filters/values)")
        elif w.chart.type == "kpi" and data is not None and len(data) != 1:
            errors.append(f"widget '{w.id}': a kpi must aggregate to exactly one row (got {len(data)})")
    return errors


def _drop_broken(df: pd.DataFrame, draft: DashboardDraft, model: SemanticModel) -> tuple[DashboardDraft, list[str]]:
    errors = _widget_errors(df, draft, model)
    if not errors:
        return draft, []
    broken = {e.split("'")[1] for e in errors if e.startswith("widget '")}
    kept = [w for w in draft.widgets if w.id not in broken]
    kept_ids = {w.id for w in kept}
    kept = [w for w in kept if not (w.type == "text" and w.text.mode == "generated" and w.text.source_widget_id
                                    and w.text.source_widget_id not in kept_ids)]
    draft.widgets = kept
    return draft, errors


def design_dashboard(df: pd.DataFrame, profile: TableProfile, model: SemanticModel, llm: LLMClient | None,
                     app_version: str, created_by: str = "", progress=None) -> tuple[DashboardSpec, list[str]]:
    """Returns (spec, warnings). Falls back to the heuristic dashboard when the LLM is unavailable or fails."""
    warnings: list[str] = []
    draft: DashboardDraft | None = None
    if llm is not None:
        try:
            if progress:
                progress("Designing the dashboard with the AI model…")
            res = call_structured(llm, system=prompts.DASHBOARD_SYSTEM, user=prompts.dashboard_design_prompt(profile, model),
                                  model_cls=DashboardDraft, schema_name="dashboard_draft", max_tokens=24_000,
                                  validate_extra=lambda d: _widget_errors(df, d, model))
            draft = res.obj  # type: ignore[assignment]
            if res.errors:
                draft, errs = _drop_broken(df, draft, model)
                warnings.extend(f"Dropped a chart the AI could not get right: {e}" for e in errs)
        except LLMError as exc:
            log.warning("dashboard design failed, using fallback: %s", exc)
            warnings.append(f"The AI could not design the dashboard ({exc}); showing an automatic default instead.")
            draft = None
    if draft is None or not draft.widgets:
        draft = default_dashboard(profile, model)
        draft, errs = _drop_broken(df, draft, model)
        warnings.extend(errs)
        if llm is None:
            warnings.append("No AI model is configured: this is an automatic default dashboard built from the column roles.")
    return draft_to_spec(draft, profile, model, None, llm, app_version, created_by), warnings


def refine_dashboard(df: pd.DataFrame, profile: TableProfile, spec: DashboardSpec, message: str, history: list[dict],
                     llm: LLMClient | None, app_version: str) -> tuple[DashboardSpec, str, list[str]]:
    """Apply a chat instruction to the spec. Returns (new_spec, reply, warnings)."""
    model = spec.semantic_model
    if llm is None:
        return spec, ("No AI model is configured, so I can't interpret change requests. You can still remove, "
                      "reorder and download the dashboard."), []
    res = call_structured(llm, system=prompts.DASHBOARD_REFINE_SYSTEM,
                          user=prompts.dashboard_refine_prompt(profile, model, spec, history, message),
                          model_cls=DashboardRefinement, schema_name="dashboard_refinement", max_tokens=24_000,
                          validate_extra=lambda r: _widget_errors(df, r.draft, model))
    refined: DashboardRefinement = res.obj  # type: ignore[assignment]
    warnings: list[str] = []
    draft = refined.draft
    if res.errors:
        draft, errs = _drop_broken(df, draft, model)
        warnings.extend(f"Dropped a chart that could not be computed: {e}" for e in errs)
    _carry_over_text(spec, draft)
    new_spec = draft_to_spec(draft, profile, model, spec, llm, app_version)
    return new_spec, refined.reply or "Done.", warnings


def _carry_over_text(old: DashboardSpec, draft: DashboardDraft) -> None:
    """Keep already-generated narratives when the LLM returns the text widget with empty content."""
    for w in draft.widgets:
        if w.type != "text" or w.text.mode != "generated" or w.text.content:
            continue
        prev = old.widget(w.id)
        if prev and prev.type == "text" and prev.text.content and prev.text.source_widget_id == w.text.source_widget_id:
            w.text = prev.text.model_copy()


def mark_narratives_stale(spec: DashboardSpec) -> int:
    n = 0
    for w in spec.widgets:
        if w.type == "text" and w.text.mode == "generated" and w.text.content:
            w.text.stale = True
            n += 1
    return n


def widgets_changed(old: DashboardSpec, new: DashboardSpec) -> dict[str, list[str]]:
    old_map = {w.id: w for w in old.widgets}
    new_map = {w.id: w for w in new.widgets}
    added = [w.title or w.id for w in new.widgets if w.id not in old_map]
    removed = [w.title or w.id for w in old.widgets if w.id not in new_map]
    modified = [w.title or w.id for w in new.widgets
                if w.id in old_map and _signature(old_map[w.id]) != _signature(w)]
    return {"added": added, "removed": removed, "modified": modified}


def _signature(w: Widget) -> str:
    return w.model_dump_json(exclude={"text": {"generated_at", "stale"}})
