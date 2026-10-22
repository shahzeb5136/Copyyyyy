"""Build, render, refine and replay dashboards; generate narratives; ask questions."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ..auth import Principal, current_user
from ..deps import AppState, get_state
from ..engine.compat import check_compatibility
from ..engine.dashboard import build_dashboard
from ..errors import SpecError
from ..inference.ask import answer_question
from ..inference.dashboard import design_dashboard, mark_narratives_stale, refine_dashboard, widgets_changed
from ..inference.narrative import generate_narratives
from ..spec.migrations import load_spec
from ..spec.models import DashboardSpec
from .schemas import (
    AskRequest,
    BuildRequest,
    DashboardPayload,
    JobSubmitted,
    NarrativesRequest,
    RefineDashboardRequest,
    RenderRequest,
    ReplayResponse,
)

router = APIRouter()


def _render(state: AppState, dataset_id: str, spec: DashboardSpec):
    df = state.datasets.frame(dataset_id)
    return build_dashboard(df, spec, state.settings.max_chart_rows, state.settings.max_table_rows)


def _payload(state: AppState, dataset_id: str, spec: DashboardSpec, warnings: list[str], reply: str = "",
             changes: dict | None = None) -> dict:
    state.datasets.save_spec(dataset_id, spec)
    dashboard = _render(state, dataset_id, spec)
    return DashboardPayload(spec=spec.to_json_dict(), dashboard=dashboard, warnings=warnings + dashboard.warnings,
                            reply=reply, changes=changes or {}).model_dump(mode="json")


@router.post("/datasets/{dataset_id}/build", response_model=JobSubmitted)
def build(dataset_id: str, body: BuildRequest, state: AppState = Depends(get_state),
          user: Principal = Depends(current_user)) -> JobSubmitted:
    svc = state.datasets
    profile = svc.profile(dataset_id)
    created_by = user.display_name

    def work(report):
        df = svc.frame(dataset_id)
        svc.save_model(dataset_id, body.model)
        spec, warnings = design_dashboard(df, profile, body.model, state.llm, state.settings.app_version, created_by,
                                          progress=report)
        if body.generate_narratives and state.llm is not None:
            _, nw = generate_narratives(df, spec, state.llm, progress=report)
            warnings.extend(nw)
        report("Rendering charts…")
        return _payload(state, dataset_id, spec, warnings)

    job = state.jobs.submit("build", work, dataset_id)
    return JobSubmitted(job_id=job.id, status=job.status)


@router.post("/datasets/{dataset_id}/render", response_model=DashboardPayload)
def render(dataset_id: str, body: RenderRequest, state: AppState = Depends(get_state)) -> dict:
    """Deterministic re-render after client-side spec edits (remove/reorder widgets). No LLM."""
    return _payload(state, dataset_id, body.spec, [])


@router.post("/datasets/{dataset_id}/refine", response_model=JobSubmitted)
def refine(dataset_id: str, body: RefineDashboardRequest, state: AppState = Depends(get_state)) -> JobSubmitted:
    svc = state.datasets
    profile = svc.profile(dataset_id)
    history = [t.model_dump() for t in body.history]

    def work(report):
        df = svc.frame(dataset_id)
        report("Applying your change…")
        spec, reply, warnings = refine_dashboard(df, profile, body.spec, body.message, history, state.llm,
                                                 state.settings.app_version)
        changes = widgets_changed(body.spec, spec)
        if body.generate_narratives and state.llm is not None:
            _, nw = generate_narratives(df, spec, state.llm, progress=report)
            warnings.extend(nw)
        report("Rendering charts…")
        return _payload(state, dataset_id, spec, warnings, reply, changes)

    job = state.jobs.submit("refine", work, dataset_id)
    return JobSubmitted(job_id=job.id, status=job.status)


@router.post("/datasets/{dataset_id}/narratives", response_model=JobSubmitted)
def narratives(dataset_id: str, body: NarrativesRequest, state: AppState = Depends(get_state)) -> JobSubmitted:
    svc = state.datasets
    svc.profile(dataset_id)

    def work(report):
        df = svc.frame(dataset_id)
        spec = body.spec
        n, warnings = generate_narratives(df, spec, state.llm, body.widget_ids, body.force, progress=report)
        reply = f"Wrote {n} summar{'y' if n == 1 else 'ies'}." if n else "Nothing to write."
        return _payload(state, dataset_id, spec, warnings, reply)

    job = state.jobs.submit("narratives", work, dataset_id)
    return JobSubmitted(job_id=job.id, status=job.status)


@router.post("/replay", response_model=ReplayResponse)
async def replay(file: UploadFile = File(...), spec: str = Form(...), state: AppState = Depends(get_state),
                 user: Principal = Depends(current_user)) -> dict:
    """New data file + saved spec -> dashboard. No LLM call."""
    try:
        data = json.loads(spec)
    except json.JSONDecodeError as exc:
        raise SpecError("The dashboard file is not valid JSON.") from exc
    loaded = load_spec(data)
    content = await file.read()
    sheet = loaded.source_fingerprint.sheet or None
    info = state.datasets.create(content, file.filename or "upload.csv", created_by=user.display_name)
    if sheet and sheet in info.sheet_names and info.sheet != sheet:
        info = state.datasets.select_sheet(info.dataset_id, sheet)
    profile = state.datasets.profile(info.dataset_id)
    report = check_compatibility(loaded, profile)
    warnings = list(report.messages)
    loaded.source_fingerprint = profile.fingerprint()
    if mark_narratives_stale(loaded):
        warnings.append("Written summaries come from the previous data; use 'Refresh summaries' to rewrite them.")
    state.datasets.save_model(info.dataset_id, loaded.semantic_model)
    state.datasets.save_spec(info.dataset_id, loaded)
    dashboard = _render(state, info.dataset_id, loaded)
    if dashboard.widgets and all(w.status == "error" for w in dashboard.widgets if w.type == "chart"):
        raise SpecError("None of the dashboard's charts can be built from this file: " + "; ".join(report.messages[:3]),
                        details=report.model_dump(mode="json"))
    return ReplayResponse(dataset=info, spec=loaded.to_json_dict(), dashboard=dashboard, compatibility=report,
                          warnings=warnings + dashboard.warnings).model_dump(mode="json")


@router.post("/datasets/{dataset_id}/ask", response_model=JobSubmitted)
def ask(dataset_id: str, body: AskRequest, state: AppState = Depends(get_state)) -> JobSubmitted:
    svc = state.datasets
    profile = svc.profile(dataset_id)
    history = [h.model_dump() for h in body.history]

    def work(report):
        df = svc.frame(dataset_id)
        result = answer_question(df, profile, body.model, body.question, history, state.llm,
                                 state.settings.max_table_rows, progress=report)
        return result.model_dump(mode="json")

    job = state.jobs.submit("ask", work, dataset_id)
    return JobSubmitted(job_id=job.id, status=job.status)
