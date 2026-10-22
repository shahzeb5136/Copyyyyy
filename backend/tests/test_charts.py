from __future__ import annotations

import json

import pandas as pd
import pytest

from app.engine.charts import ChartError, build_figure, format_value, render_with_fallback, suggest_chart
from app.spec.models import ChartConfig


@pytest.fixture()
def df():
    return pd.DataFrame({"dept": ["IT", "HR", "Ops", "Fin"], "budget": [100.0, 200.0, 150.0, 50.0],
                         "actual": [120.0, 150.0, 160.0, 40.0], "share": [25.0, 35.0, 30.0, 10.0]})


@pytest.mark.parametrize("cfg", [
    ChartConfig(type="bar", x="dept", y=["budget", "actual"], value_format="currency"),
    ChartConfig(type="bar", x="dept", y=["budget"], orientation="h", sort="desc", show_values=True),
    ChartConfig(type="pie", x="dept", y=["budget"]),
    ChartConfig(type="treemap", path=["dept"], y=["budget"]),
    ChartConfig(type="waterfall", x="dept", y=["budget"]),
    ChartConfig(type="funnel", x="dept", y=["budget"]),
    ChartConfig(type="scatter", x="budget", y=["actual"]),
    ChartConfig(type="histogram", x="budget"),
    ChartConfig(type="box", x="dept", y=["budget"]),
])
def test_chart_types_render_json(cfg, df):
    res = build_figure(cfg, df, "AED")
    assert res.kind == "plotly" and res.figure["data"]
    json.dumps(res.figure)  # JSON-serialisable


def test_line_area_time_axis():
    ts = pd.DataFrame({"month": pd.date_range("2025-01-01", periods=6, freq="MS"), "v": [1, 3, 2, 5, 4, 6], "w": [2, 2, 2, 2, 2, 2]})
    for t in ("line", "area"):
        res = build_figure(ChartConfig(type=t, x="month", y=["v", "w"]), ts)
        assert res.kind == "plotly" and len(res.figure["data"]) == 2


def test_heatmap_and_table(df):
    hm = pd.DataFrame({"m": ["Jan", "Feb", "Jan", "Feb"], "d": ["IT", "IT", "HR", "HR"], "v": [1, 2, 3, 4]})
    assert build_figure(ChartConfig(type="heatmap", x="m", y=["d"], z="v"), hm).kind == "plotly"
    t = build_figure(ChartConfig(type="table"), df, max_table_rows=2)
    assert t.table["truncated"] and len(t.table["rows"]) == 2 and t.table["row_count"] == 4


def test_kpi_formatting(df):
    res = build_figure(ChartConfig(type="kpi", y=["budget"], value_format="currency"), df.head(1), "USD")
    assert res.kpi["value_formatted"] == "$100" and res.kpi["label"] == "Budget"
    assert format_value(1_250_000, "currency", "AED") == "AED 1.25M"
    assert format_value(12.345, "percent") == "12.3%"
    assert format_value(None) == "—"


def test_fallback_when_chart_impossible(df):
    res, problem = render_with_fallback(ChartConfig(type="scatter", x="dept", y=["budget"]), df, "", 100)
    assert res.kind == "plotly" and "showing a bar" in res.note and problem
    with pytest.raises(ChartError):
        build_figure(ChartConfig(type="bar", x="dept", y=["budget"]), df.iloc[0:0])


def test_suggest_chart(df):
    assert suggest_chart(df.head(1)).type == "kpi"
    assert suggest_chart(df).type == "bar"
