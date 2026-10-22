"""Application-wide singletons (storage, jobs, LLM, dataset service) and FastAPI dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Request

from .jobs import JobManager
from .llm_client import LLMClient, build_llm_client
from .services import DatasetService
from .settings import Settings, get_settings
from .storage import Storage, build_storage

log = logging.getLogger("dashboard")


@dataclass
class AppState:
    settings: Settings
    storage: Storage
    jobs: JobManager
    datasets: DatasetService
    llm: LLMClient | None


def build_state(settings: Settings | None = None) -> AppState:
    settings = settings or get_settings()
    storage = build_storage(settings)
    llm = build_llm_client(settings)
    if llm is None:
        log.warning("No LLM configured (LLM_PROVIDER=%s): running with heuristics only.", settings.effective_llm_provider)
    else:
        log.info("LLM provider=%s model=%s", llm.provider_name, llm.model)
    return AppState(settings=settings, storage=storage, jobs=JobManager(storage, settings.job_workers),
                    datasets=DatasetService(storage, settings), llm=llm)


def get_state(request: Request) -> AppState:
    return request.app.state.ctx
