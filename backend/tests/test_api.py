from __future__ import annotations

import io
import json

from tests.conftest import wait_job


def _upload(client, content: bytes, name: str):
    return client.post("/api/datasets", files={"file": (name, io.BytesIO(content), "application/octet-stream")})


def test_health_and_me(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["llm"]["configured"] is False and h["storage"] == "local"
    assert client.get("/api/me").json()["authenticated"] is False
    me = client.get("/api/me", headers={"x-ms-client-principal-name": "jane@contoso.com"}).json()
    assert me["authenticated"] and me["email"] == "jane@contoso.com"


def test_bad_header_message(client):
    r = _upload(client, b"Budget report 2026\nDept,Amount,Year\nIT,5,2025\nHR,6,2025\nOps,7,2026\n", "bad.csv")
    assert r.status_code == 400 and "column names in the first row" in r.json()["detail"]
    r = _upload(client, b"x", "bad.docx")
    assert r.status_code == 400 and "Unsupported" in r.json()["detail"]


def test_full_flow_without_llm(client, budget_bytes):
    up = _upload(client, budget_bytes, "budget_actuals.csv")
    assert up.status_code == 200, up.text
    info = up.json()
    ds = info["dataset_id"]
    assert info["row_count"] == 1584 and len(info["preview"]["rows"]) == 10
    assert client.get(f"/api/datasets/{ds}").json()["file_name"] == "budget_actuals.csv"

    job = wait_job(client, client.post(f"/api/datasets/{ds}/infer").json()["job_id"])
    assert job["status"] == "succeeded", job
    model = job["result"]["model"]
    assert model["domain"] == "finance" and "No AI" in job["result"]["reply"]

    job = wait_job(client, client.post(f"/api/datasets/{ds}/refine-model", json={"model": model, "message": "Budget excludes capex", "history": []}).json()["job_id"])
    model = job["result"]["model"]
    assert model["business_notes"] == ["Budget excludes capex"]

    job = wait_job(client, client.post(f"/api/datasets/{ds}/build", json={"model": model}).json()["job_id"])
    assert job["status"] == "succeeded", job
    payload = job["result"]
    spec = payload["spec"]
    assert spec["spec_version"] == "1.0" and spec["semantic_model"]["business_notes"] == ["Budget excludes capex"]
    widgets = payload["dashboard"]["widgets"]
    assert widgets and all(w["status"] in ("ok", "pending") for w in widgets)

    # deterministic re-render after a client-side edit (remove the last widget)
    spec2 = json.loads(json.dumps(spec))
    spec2["widgets"] = spec2["widgets"][:-1]
    r = client.post(f"/api/datasets/{ds}/render", json={"spec": spec2})
    assert r.status_code == 200 and len(r.json()["dashboard"]["widgets"]) == len(widgets) - 1

    # replay with the same file: identical figures
    rp = client.post("/api/replay", files={"file": ("budget_actuals.csv", io.BytesIO(budget_bytes))}, data={"spec": json.dumps(spec)})
    assert rp.status_code == 200, rp.text
    replay = rp.json()
    assert replay["compatibility"]["ok"] and replay["dataset"]["dataset_id"] != ds
    a = [(w["id"], w["figure"], w["kpi"]) for w in widgets]
    b = [(w["id"], w["figure"], w["kpi"]) for w in replay["dashboard"]["widgets"]]
    assert a == b

    # replay with a file missing a used column -> report + per-widget errors
    text = budget_bytes.decode()
    broken = "\n".join(",".join(line.split(",")[:-1]) for line in text.splitlines())  # drop period_status
    broken = broken.replace("actual_usd", "spend")
    rp = client.post("/api/replay", files={"file": ("new.csv", io.BytesIO(broken.encode()))}, data={"spec": json.dumps(spec)})
    assert rp.status_code in (200, 422)
    if rp.status_code == 200:
        comp = rp.json()["compatibility"]
        assert not comp["ok"] and "actual_usd" in comp["missing_required"]

    # ask without an LLM is a clean failure
    job = wait_job(client, client.post(f"/api/datasets/{ds}/ask", json={"question": "total?", "model": model, "history": []}).json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["can_answer"] is False


def test_replay_rejects_bad_spec(client, budget_bytes):
    rp = client.post("/api/replay", files={"file": ("b.csv", io.BytesIO(budget_bytes))}, data={"spec": "{not json"})
    assert rp.status_code == 422 and "JSON" in rp.json()["detail"]
    rp = client.post("/api/replay", files={"file": ("b.csv", io.BytesIO(budget_bytes))}, data={"spec": json.dumps({"spec_version": "0.1"})})
    assert rp.status_code == 422 and "Unsupported" in rp.json()["detail"]


def test_excel_sheet_selection(client, xlsx_bytes):
    info = _upload(client, xlsx_bytes, "book.xlsx").json()
    assert info["sheet_names"] == ["Data", "Notes"] and info["sheet"] == "Data" and info["row_count"] == 18
    r = client.post(f"/api/datasets/{info['dataset_id']}/sheet", json={"sheet": "Notes"})
    assert r.status_code == 200 and r.json()["column_count"] == 1
    r = client.post(f"/api/datasets/{info['dataset_id']}/sheet", json={"sheet": "Missing"})
    assert r.status_code == 400


def test_unknown_dataset_and_job(client):
    assert client.get("/api/datasets/deadbeef").status_code == 404
    assert client.get("/api/jobs/nothing").status_code == 404
    assert client.post("/api/datasets/deadbeef/infer").status_code == 404


def test_samples_and_schema(client):
    names = [s["name"] for s in client.get("/api/samples").json()]
    assert "budget_actuals.csv" in names
    assert client.get("/api/samples/budget_actuals.csv").status_code == 200
    assert client.get("/api/samples/../pyproject.toml").status_code in (404, 400)
    assert "widgets" in client.get("/api/spec-schema").json()["properties"]


def test_upload_size_limit(tmp_path, budget_bytes):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings

    s = Settings(llm_provider="mock", storage_local_dir=str(tmp_path / "d"), max_upload_mb=0, retention_hours=0, _env_file=None)
    with TestClient(create_app(s)) as c:
        r = _upload(c, budget_bytes, "b.csv")
        assert r.status_code == 413
