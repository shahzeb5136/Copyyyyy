from __future__ import annotations

import pandas as pd
import pytest

from app.engine.expressions import ExpressionError, evaluate_expression, referenced_columns


@pytest.fixture()
def df():
    return pd.DataFrame({"budget": [100.0, 200.0, 0.0], "actual": [120.0, 150.0, 5.0], "Net Sales (AED)": [1.0, 2.0, 3.0],
                         "d": pd.to_datetime(["2025-01-01", "2025-06-15", "2026-03-01"]), "status": ["Open", "Closed", "open"]})


def test_arithmetic_and_div_by_zero(df):
    assert evaluate_expression("actual - budget", df).tolist() == [20, -50, 5]
    out = evaluate_expression("100 * (actual - budget) / budget", df)
    assert out.tolist()[:2] == [20.0, -25.0] and pd.isna(out.iloc[2])


def test_backticks_and_case_insensitive(df):
    assert evaluate_expression("`Net Sales (AED)` * 2", df).tolist() == [2, 4, 6]
    assert evaluate_expression("BUDGET + 1", df).tolist() == [101, 201, 1]


def test_functions(df):
    assert evaluate_expression("if(status == 'open', 1, 0)", df).tolist() == [1, 0, 1]
    assert evaluate_expression("year(d)", df).tolist() == [2025, 2025, 2026]
    assert evaluate_expression("round(actual / 7, 1)", df).tolist() == [17.1, 21.4, 0.7]
    assert evaluate_expression("coalesce(budget, 0) + 1", df).tolist() == [101, 201, 1]
    assert evaluate_expression("max(budget, actual)", df).tolist() == [120, 200, 5]
    assert evaluate_expression("abs(budget - actual) > 10 and status != 'x'", df).tolist() == [True, True, False]


def test_unsafe_constructs_rejected(df):
    for bad in ["__import__('os')", "budget.__class__", "exec('x')", "budget[0]", "open('f')", "lambda: 1"]:
        with pytest.raises(ExpressionError):
            evaluate_expression(bad, df)


def test_unknown_column_message(df):
    with pytest.raises(ExpressionError, match="Unknown column 'nope'"):
        evaluate_expression("nope + 1", df)


def test_referenced_columns():
    assert referenced_columns("100 * (actual - `Net Sales (AED)`) / budget") == ["actual", "Net Sales (AED)", "budget"]
