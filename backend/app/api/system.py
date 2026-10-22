"""Health, identity, jobs, schema and sample files."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import Principal, current_user
from ..deps import AppState, get_state
from ..jobs import JobRecord
from ..spec.models import SPEC_VERSION, DashboardSpec

router = APIRouter()


@router.get("/health")
def health(state: AppState = Depends(get_state)) -> dict:
    s = state.settings
    return {"status": "ok", "app": s.app_name, "version": s.app_version, "spec_version": SPEC_VERSION,
            "llm": {"configured": state.llm is not None, "provider": state.llm.provider_name if state.llm else "none",
                    "model": state.llm.model if state.llm else ""},
            "storage": state.storage.mode}


@router.get("/me")
def me(user: Principal = Depends(current_user)) -> dict:
    return user.as_dict()


@router.get("/jobs/{job_id}", response_model=JobRecord)
def get_job(job_id: str, state: AppState = Depends(get_state)) -> JobRecord:
    job = state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.get("/spec-schema")
def spec_schema() -> dict:
    return DashboardSpec.model_json_schema(by_alias=True)


@router.get("/samples")
def list_samples(state: AppState = Depends(get_state)) -> list[dict]:
    samples_dir = state.settings.resolved_samples_dir()
    if not samples_dir.exists():
        return []
    out = []
    for p in sorted(samples_dir.iterdir()):
        if p.suffix.lower() in {".csv", ".xlsx", ".json"} and p.is_file():
            out.append({"name": p.name, "size": p.stat().st_size})
    return out


@router.get("/samples/{name}")
def get_sample(name: str, state: AppState = Depends(get_state)):
    safe = Path(name).name
    path = state.settings.resolved_samples_dir() / safe
    if not path.is_file() or path.suffix.lower() not in {".csv", ".xlsx", ".json"}:
        raise HTTPException(status_code=404, detail="Sample not found.")
    return FileResponse(path, filename=safe)
