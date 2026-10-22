"""Upload a file, pick a sheet, infer and refine the semantic model."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from ..auth import Principal, current_user
from ..deps import AppState, get_state
from ..inference.semantic import infer_semantic_model, refine_semantic_model
from .schemas import DatasetInfo, JobSubmitted, RefineModelRequest, SheetRequest

router = APIRouter()


@router.post("/datasets", response_model=DatasetInfo)
async def upload_dataset(file: UploadFile = File(...), state: AppState = Depends(get_state),
                         user: Principal = Depends(current_user)) -> DatasetInfo:
    content = await file.read()
    return state.datasets.create(content, file.filename or "upload.csv", created_by=user.display_name)


@router.get("/datasets/{dataset_id}", response_model=DatasetInfo)
def get_dataset(dataset_id: str, state: AppState = Depends(get_state)) -> DatasetInfo:
    return state.datasets.info(dataset_id)


@router.post("/datasets/{dataset_id}/sheet", response_model=DatasetInfo)
def select_sheet(dataset_id: str, body: SheetRequest, state: AppState = Depends(get_state)) -> DatasetInfo:
    return state.datasets.select_sheet(dataset_id, body.sheet)


@router.post("/datasets/{dataset_id}/infer", response_model=JobSubmitted)
def infer_model(dataset_id: str, state: AppState = Depends(get_state)) -> JobSubmitted:
    svc = state.datasets
    profile = svc.profile(dataset_id)  # 404 early if unknown

    def work(report):
        report("Analysing columns…")
        model, reply = infer_semantic_model(profile, state.llm)
        svc.save_model(dataset_id, model)
        return {"model": model.model_dump(mode="json"), "reply": reply}

    job = state.jobs.submit("infer", work, dataset_id)
    return JobSubmitted(job_id=job.id, status=job.status)


@router.post("/datasets/{dataset_id}/refine-model", response_model=JobSubmitted)
def refine_model(dataset_id: str, body: RefineModelRequest, state: AppState = Depends(get_state)) -> JobSubmitted:
    svc = state.datasets
    profile = svc.profile(dataset_id)
    history = [t.model_dump() for t in body.history]

    def work(report):
        report("Updating the semantic model…")
        model, reply = refine_semantic_model(profile, body.model, body.message, history, state.llm)
        svc.save_model(dataset_id, model)
        return {"model": model.model_dump(mode="json"), "reply": reply}

    job = state.jobs.submit("refine_model", work, dataset_id)
    return JobSubmitted(job_id=job.id, status=job.status)
