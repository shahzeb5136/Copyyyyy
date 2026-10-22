"""Background jobs for the slow (LLM) steps.

``POST`` endpoints that call the LLM return a ``job_id`` immediately; the front end
polls ``GET /api/jobs/{id}``. Job state is persisted through the storage layer so
that any replica can answer the poll, even though the work runs on the replica
that accepted it.
"""

from __future__ import annotations

import logging
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

from .errors import UserFacingError
from .storage import Storage

log = logging.getLogger("dashboard.jobs")

JobStatus = Literal["queued", "running", "succeeded", "failed"]


class JobRecord(BaseModel):
    id: str
    kind: str
    status: JobStatus = "queued"
    created_at: str = ""
    updated_at: str = ""
    progress: str = ""
    result: Any = None
    error: str = ""
    dataset_id: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobManager:
    def __init__(self, storage: Storage, workers: int = 4):
        self.storage = storage
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="job")
        self._local: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def submit(self, kind: str, fn: Callable[[Callable[[str], None]], Any], dataset_id: str = "") -> JobRecord:
        job = JobRecord(id=uuid.uuid4().hex, kind=kind, created_at=_now(), updated_at=_now(), dataset_id=dataset_id)
        self._save(job)
        self._pool.submit(self._run, job.id, fn)
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._local.get(job_id)
        if job is not None:
            return job
        key = f"jobs/{job_id}.json"
        if not self.storage.exists(key):
            return None
        try:
            return JobRecord.model_validate(self.storage.get_json(key))
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    def _save(self, job: JobRecord) -> None:
        job.updated_at = _now()
        with self._lock:
            self._local[job.id] = job
        try:
            self.storage.put_json(f"jobs/{job.id}.json", job.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - keep serving from memory
            log.warning("could not persist job %s: %s", job.id, exc)

    def _run(self, job_id: str, fn: Callable[[Callable[[str], None]], Any]) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = "running"
        self._save(job)

        def report(progress: str) -> None:
            job.progress = progress
            self._save(job)

        try:
            result = fn(report)
            job.result = result
            job.status = "succeeded"
            job.progress = "done"
        except UserFacingError as exc:
            job.status = "failed"
            job.error = str(exc)
            log.info("job %s (%s) failed: %s", job.id, job.kind, exc)
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = f"Unexpected error ({type(exc).__name__}): {str(exc)[:500]}"
            log.error("job %s (%s) crashed:\n%s", job.id, job.kind, traceback.format_exc())
        self._save(job)
