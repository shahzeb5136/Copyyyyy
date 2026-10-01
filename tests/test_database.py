"""The SQL sandbox: only single read-only SELECTs, no file/network access, limits enforced."""

import os

import pandas as pd
import pytest

from core.catalog import Column, Dataset, Table
from core.database import QueryEngine, QueryError, clean_sql


def test_simple_select(engines):
    res = engines["finance"].execute("SELECT COUNT(*) AS n FROM budget_actuals")
    assert res.df.iloc[0]["n"] == 1584
    assert not res.truncated


def test_dates_are_real_dates(engines):
    res = engines["projects"].execute(
        "SELECT project_name, date_diff('day', planned_end_date, forecast_end_date) AS slip "
        "FROM projects WHERE forecast_end_date IS NOT NULL ORDER BY slip DESC LIMIT 1")
    assert res.df.iloc[0]["project_name"] == "Data Warehouse Modernization"


def test_cte_and_trailing_semicolon(engines):
    res = engines["hr"].execute("WITH a AS (SELECT * FROM employees) SELECT COUNT(*) AS n FROM a;;")
    assert res.df.iloc[0]["n"] == 579


@pytest.mark.parametrize("sql", [
    "DROP TABLE employees",
    "DELETE FROM employees",
    "INSERT INTO departments VALUES ('x','y','z','w','v', 1)",
    "UPDATE employees SET base_salary_usd = 0",
    "CREATE TABLE x AS SELECT 1",
    "SELECT 1; DROP TABLE employees",
    "PRAGMA threads=8",
    "SET enable_external_access = true",
    "ATTACH '/tmp/x.db'",
    "COPY employees TO '/tmp/out.csv'",
    "INSTALL httpfs",
])
def test_non_select_statements_are_rejected(engines, sql):
    with pytest.raises(QueryError):
        engines["hr"].execute(sql)
    # and the data is untouched
    assert engines["hr"].execute("SELECT COUNT(*) AS n FROM employees").df.iloc[0]["n"] == 579


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_text('/etc/hostname')",
    "SELECT * FROM glob('/*')",
    "SELECT * FROM 'data/hr/employees.csv'",
])
def test_file_access_is_blocked(engines, sql):
    with pytest.raises(QueryError):
        engines["hr"].execute(sql)


def test_environment_is_not_readable(engines):
    os.environ["ASKDATA_TEST_SECRET"] = "top-secret"
    with pytest.raises(QueryError):
        engines["hr"].execute("SELECT getenv('ASKDATA_TEST_SECRET') AS s")


def test_row_limit_and_truncation(engines):
    res = engines["hr"].execute("SELECT * FROM leave_records")
    assert res.truncated and res.row_count == 1_000


def test_timeout():
    ds = Dataset("t", "t", "t", [Table("t", "t", [Column("a", "integer")], pd.DataFrame({"a": [1]}))])
    eng = QueryEngine(ds, timeout_seconds=1)
    with pytest.raises(QueryError) as exc:
        eng.execute("SELECT COUNT(*) FROM range(100000000000) a, range(1000) b")
    assert exc.value.kind == "timeout"


def test_helpful_error_for_bad_column(engines):
    with pytest.raises(QueryError) as exc:
        engines["finance"].execute("SELECT nope FROM budget_actuals")
    assert "nope" in str(exc.value)


def test_clean_sql_strips_fences():
    assert clean_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
