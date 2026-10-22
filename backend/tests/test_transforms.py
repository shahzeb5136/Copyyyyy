from __future__ import annotations

import pandas as pd
import pytest

from app.engine.pipeline import QueryError, columns_referenced, run_steps
from app.spec.models import Condition, Metric, Rename, Step


def run(df, *steps):
    return run_steps(df, list(steps)).df


def test_filter_year_shortcut_and_aggregate(budget_table):
    df = budget_table.df
    out = run(df, Step(op="filter", conditions=[Condition(column="period_month", operator="eq", value="2026")]),
              Step(op="aggregate", metrics=[Metric(column="budget_usd", fn="sum", name="budget"), Metric(fn="count", name="rows")]))
    assert out.shape == (1, 2) and out["rows"].iloc[0] == 792


def test_filter_text_case_insensitive_and_in(budget_table):
    df = budget_table.df
    a = run(df, Step(op="filter", conditions=[Condition(column="expense_category", operator="eq", value="salaries & wages")]))
    b = run(df, Step(op="filter", conditions=[Condition(column="expense_category", operator="in", values=["Salaries & Wages", "Benefits"])]))
    assert len(a) == 192 and len(b) == 384


def test_date_bucket_quarter_and_period_change(budget_table):
    out = run(budget_table.df,
              Step(op="date_bucket", column="period_month", unit="quarter", name="q"),
              Step(op="aggregate", group_by=["q"], metrics=[Metric(column="actual_usd", fn="sum", name="actual")]),
              Step(op="sort", columns=["q"], descending=False),
              Step(op="period_change", column="actual", name="qoq", kind="pct"))
    assert out["q"].tolist()[:2] == ["2025-Q1", "2025-Q2"] and pd.isna(out["qoq"].iloc[0])


def test_top_n_with_other_and_pct_of_total(budget_table):
    out = run(budget_table.df,
              Step(op="aggregate", group_by=["expense_category"], metrics=[Metric(column="actual_usd", fn="sum", name="actual")]),
              Step(op="top_n", column="actual", n=3, value="Other"),
              Step(op="pct_of_total", column="actual", name="share"))
    assert len(out) == 4 and out["expense_category"].iloc[-1] == "Other"
    assert abs(out["share"].sum() - 100) < 1e-6


def test_pivot_unpivot_rename_select_cast(budget_table):
    df = budget_table.df
    p = run(df, Step(op="pivot", group_by=["expense_category"], column="fiscal_year", value="actual_usd", fn="sum"))
    assert set(p.columns) == {"expense_category", "2025", "2026"}
    u = run(p, Step(op="unpivot", group_by=["expense_category"], columns=["2025", "2026"], name="year", value="actual"))
    assert len(u) == len(p) * 2
    r = run(u, Step(op="rename", renames=[Rename(**{"from": "actual", "to": "Actual (USD)"})]),
            Step(op="select", columns=["year", "Actual (USD)"]), Step(op="cast", column="year", unit="number"))
    assert list(r.columns) == ["year", "Actual (USD)"] and r["year"].dtype.kind == "f"


def test_derive_bin_rank_cumulative(budget_table):
    out = run(budget_table.df,
              Step(op="derive", name="variance", expression="actual_usd - budget_usd"),
              Step(op="aggregate", group_by=["department_id"], metrics=[Metric(column="variance", fn="sum", name="variance")]),
              Step(op="sort", columns=["variance"], descending=True),
              Step(op="rank", column="variance", name="rk"),
              Step(op="cumulative", column="variance", name="cum"))
    assert out["rk"].tolist()[0] == 1 and abs(out["cum"].iloc[-1] - out["variance"].sum()) < 1e-6
    b = run(budget_table.df, Step(op="bin", column="actual_usd", n=4, name="bucket"))
    assert b["bucket"].nunique() <= 5


def test_date_part_and_fill_drop_nulls():
    df = pd.DataFrame({"d": pd.to_datetime(["2026-01-05", "2026-02-10", None]), "v": [1.0, None, 3.0]})
    out = run(df, Step(op="date_part", column="d", unit="month_name", name="mn"), Step(op="fill_nulls", column="v", value="0"))
    assert out["mn"].tolist()[:2] == ["Jan", "Feb"] and out["v"].tolist() == [1.0, 0.0, 3.0]
    assert len(run(df, Step(op="drop_nulls", columns=["d"]))) == 2


def test_errors_are_informative(budget_table):
    with pytest.raises(QueryError, match="step 1 \\(aggregate\\): group_by column 'dept' not found"):
        run(budget_table.df, Step(op="aggregate", group_by=["dept"], metrics=[Metric(column="actual_usd")]))
    with pytest.raises(QueryError, match="not numeric"):
        run(budget_table.df, Step(op="aggregate", metrics=[Metric(column="expense_category", fn="sum")]))


def test_columns_referenced_excludes_created():
    steps = [Step(op="derive", name="v", expression="a - b"), Step(op="aggregate", group_by=["c"], metrics=[Metric(column="v", name="t")]),
             Step(op="sort", columns=["t"])]
    assert columns_referenced(steps) == {"a", "b", "c"}
