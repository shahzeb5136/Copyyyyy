"""Every chart type renders from a spec, bad specs fall back gracefully."""

import pandas as pd
import pytest

from core.charts import ChartError, ChartSpec, build_chart, build_charts, format_value, pretty, suggest_spec

CAT = pd.DataFrame({"department": ["Sales", "IT", "HR", "Finance"], "budget_usd": [100.0, 80, 40, 30],
                    "actual_usd": [110.0, 70, 45, 28], "variance_pct": [10.0, -12.5, 12.5, -6.7]})
TS = pd.DataFrame({"month": pd.date_range("2026-01-01", periods=6, freq="MS"),
                   "region": ["A", "B"] * 3, "revenue_usd": [1e6, 2e6, 1.5e6, 2.5e6, 1.2e6, 2.2e6]})
GANTT = pd.DataFrame({"task": ["Plan", "Build", "Go-live"],
                      "start_date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-04-01"]),
                      "due_date": pd.to_datetime(["2026-01-31", "2026-03-31", "2026-04-01"]),
                      "status": ["Completed", "In Progress", "Not Started"]})


def spec(**kw):
    return ChartSpec.from_dict(kw)


@pytest.mark.parametrize("s, df", [
    (spec(chart_type="bar", x="department", y=["budget_usd", "actual_usd"], value_format="currency"), CAT),
    (spec(chart_type="bar", x="department", y=["variance_pct"], orientation="h", sort="desc",
          value_format="percent"), CAT),
    (spec(chart_type="line", x="month", y=["revenue_usd"], color="region"), TS),
    (spec(chart_type="area", x="month", y=["revenue_usd"], color="region"), TS),
    (spec(chart_type="pie", x="department", y=["budget_usd"]), CAT),
    (spec(chart_type="scatter", x="budget_usd", y=["actual_usd"]), CAT),
    (spec(chart_type="histogram", x="actual_usd"), CAT),
    (spec(chart_type="box", x="region", y=["revenue_usd"]), TS),
    (spec(chart_type="heatmap", x="month", y=["region"], z="revenue_usd"), TS),
    (spec(chart_type="treemap", path=["region"], y=["revenue_usd"]), TS),
    (spec(chart_type="waterfall", x="department", y=["variance_pct"]), CAT),
    (spec(chart_type="funnel", x="department", y=["budget_usd"]), CAT),
    (spec(chart_type="gantt", label="task", start="start_date", end="due_date", color="status"), GANTT),
])
def test_every_chart_type_renders(s, df):
    out = build_chart(s, df, "USD", "2026-03-01")
    assert out.kind == "plotly" and out.figure is not None
    assert len(out.figure.data) >= 1


def test_kpi():
    df = pd.DataFrame({"total_revenue_usd": [1_234_567.0], "deals": [42]})
    out = build_chart(spec(chart_type="kpi", y=["total_revenue_usd", "deals"], value_format="currency"), df)
    assert out.kind == "kpi"
    assert out.metrics[0] == ("Total revenue (USD)", "$1.23M")


def test_case_insensitive_columns():
    out = build_chart(spec(chart_type="bar", x="DEPARTMENT", y=["Budget_USD"]), CAT)
    assert out.figure is not None


def test_missing_column_raises():
    with pytest.raises(ChartError):
        build_chart(spec(chart_type="bar", x="nope", y=["budget_usd"]), CAT)


def test_build_charts_falls_back_when_spec_is_bad():
    rendered, problems = build_charts([spec(chart_type="bar", x="nope", y=["nope"])], CAT)
    assert len(rendered) == 1 and problems


def test_many_series_fold_into_other():
    df = pd.DataFrame({"m": list(range(3)) * 12, "s": [f"s{i}" for i in range(12)] * 3, "v": range(36)})
    out = build_chart(spec(chart_type="line", x="m", y=["v"], color="s"), df)
    assert "Other" in out.note


def test_status_colours_for_status_values():
    out = build_chart(spec(chart_type="gantt", label="task", start="start_date", end="due_date", color="status"),
                      GANTT)
    colours = {t.name: t.marker.color for t in out.figure.data}
    assert colours["Completed"] == "#0ca30c"


def test_suggest_spec_shapes():
    assert suggest_spec(TS).chart_type == "line"
    assert suggest_spec(CAT).chart_type == "bar"
    assert suggest_spec(pd.DataFrame({"n": [3]})).chart_type == "kpi"
    assert suggest_spec(GANTT).chart_type == "gantt"
    assert suggest_spec(pd.DataFrame()) is None


def test_formatting_helpers():
    assert pretty("total_actual_usd") == "Total actual (USD)"
    assert pretty("variance_pct") == "Variance (%)"
    assert format_value(12.345, "percent", column="x_pct") == "12.3%"
    assert format_value(-2_500_000, "currency", "USD") == "-$2.50M"
