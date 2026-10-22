from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.errors import SpecError
from app.inference.schema_tools import strict_schema
from app.spec.migrations import load_spec
from app.spec.models import ChartConfig, DashboardDraft, DashboardSpec, Metric, Step, Widget


def test_step_validation_messages():
    with pytest.raises(ValidationError, match="needs 'name' and 'expression'"):
        Step(op="derive", name="x")
    with pytest.raises(ValidationError, match="unit in year"):
        Step(op="date_bucket", column="d", unit="fortnight")
    assert Step(op="bin", column="v").n == 10


def test_widget_ids_unique_and_kpi_width():
    d = DashboardDraft(widgets=[Widget(title="Total"), Widget(title="Total"),
                                Widget(title="k", chart=ChartConfig(type="kpi", y=["v"]))])
    assert [w.id for w in d.widgets] == ["total", "total_2", "k"] and d.widgets[2].layout.w == 3


def test_round_trip_compact_json():
    w = Widget(title="Spend", steps=[Step(op="aggregate", group_by=["dept"], metrics=[Metric(column="x")])],
               chart=ChartConfig(type="bar", x="dept", y=["sum_x"]))
    spec = DashboardSpec(widgets=[w])
    data = spec.to_json_dict()
    assert data["spec_version"] == "1.0" and "text" not in data["widgets"][0]
    again = load_spec(json.loads(json.dumps(data)))
    assert again.widgets[0].steps[0].metrics[0].output_name == "sum_x"


def test_unknown_version_and_garbage_rejected():
    with pytest.raises(SpecError, match="Unsupported"):
        load_spec({"spec_version": "9.9"})
    with pytest.raises(SpecError, match="spec_version"):
        load_spec({"widgets": []})
    with pytest.raises(SpecError, match="not valid"):
        load_spec({"spec_version": "1.0", "widgets": [{"steps": [{"op": "explode"}]}]})


def test_strict_schema_shape():
    def check(node):
        if isinstance(node, dict):
            assert "$ref" not in node and "default" not in node
            if node.get("type") == "object":
                assert set(node["required"]) == set(node["properties"]) and node["additionalProperties"] is False
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    s = strict_schema(DashboardDraft)
    check(s)
    assert "from" in s["properties"]["widgets"]["items"]["properties"]["steps"]["items"]["properties"]["renames"]["items"]["properties"]
