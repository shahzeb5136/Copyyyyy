from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.engine.loader import load_table
from app.engine.profile import profile_table
from app.main import create_app
from app.settings import Settings

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


@pytest.fixture(scope="session")
def budget_bytes() -> bytes:
    return (SAMPLES / "budget_actuals.csv").read_bytes()


@pytest.fixture(scope="session")
def orders_bytes() -> bytes:
    return (SAMPLES / "online_store_orders.csv").read_bytes()


@pytest.fixture(scope="session")
def budget_table(budget_bytes):
    return load_table(budget_bytes, "budget_actuals.csv")


@pytest.fixture(scope="session")
def budget_profile(budget_table):
    return profile_table(budget_table.df, budget_table.types, "budget_actuals.csv")


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def xlsx_bytes() -> bytes:
    from datetime import datetime

    data = [["Department", "Month", "Budget", "Actual", "Closed?"]]
    for i, dept in enumerate(["IT", "HR", "Ops"]):
        for m in range(1, 7):
            data.append([dept, datetime(2026, m, 1), 1000 + 100 * i + 10 * m, 950 + 120 * i + 9 * m, m < 5])
    notes = [["Notes"], ["This workbook is synthetic."]]
    return make_xlsx({"Data": data, "Notes": notes})


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(llm_provider="mock", storage_mode="local", storage_local_dir=str(tmp_path / "data"),
                    retention_hours=0, job_workers=2, static_dir=str(tmp_path / "nostatic"), _env_file=None)


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def wait_job(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")
