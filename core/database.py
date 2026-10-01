"""A sandboxed, in-memory SQL engine over the dataset's DataFrames.

There is no real database server in this demo - the CSV files are loaded into an
in-memory DuckDB database. That gives us a *real* SQL engine (joins, window
functions, CTEs, date maths) so the AI-generated SQL actually runs.

Guardrails, because the SQL is written by an LLM and the app is public:

* Only a single read-only ``SELECT`` (or ``WITH ... SELECT``) statement is allowed.
  The statement type is determined by DuckDB's own parser, not by regex.
* File system / network access is disabled and the configuration is locked, so a
  query cannot read local files, environment variables, install extensions or
  re-enable any of that.
* Results are capped at ``max_rows`` and every query has a timeout.
* Memory and thread usage are capped.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from decimal import Decimal

import duckdb
import pandas as pd

from .catalog import DUCKDB_TYPES, Dataset

MAX_SQL_CHARS = 20_000


class QueryError(Exception):
    """A query was rejected or failed. ``kind`` is validation | execution | timeout."""

    def __init__(self, message: str, kind: str = "execution"):
        super().__init__(message)
        self.kind = kind


@dataclass
class QueryResult:
    sql: str
    df: pd.DataFrame
    truncated: bool
    elapsed_ms: float

    @property
    def row_count(self) -> int:
        return len(self.df)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def clean_sql(sql: str) -> str:
    """Strip markdown fences, surrounding whitespace and trailing semicolons."""
    s = (sql or "").strip()
    s = re.sub(r"^```(?:sql|duckdb)?\s*|\s*```$", "", s, flags=re.IGNORECASE).strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


class QueryEngine:
    def __init__(
        self,
        dataset: Dataset,
        max_rows: int = 5_000,
        timeout_seconds: float = 15.0,
        memory_limit: str = "1GB",
        threads: int = 2,
    ):
        self.dataset = dataset
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self._con = duckdb.connect(database=":memory:")
        self._con.execute(f"SET memory_limit = '{memory_limit}'")
        self._con.execute(f"SET threads = {int(threads)}")
        for table in dataset.tables:
            src = f"__src_{table.name}"
            self._con.register(src, table.df)
            select_list = ", ".join(
                f"CAST({_quote(c.name)} AS {DUCKDB_TYPES[c.type]}) AS {_quote(c.name)}" for c in table.columns
            )
            self._con.execute(f"CREATE TABLE {_quote(table.name)} AS SELECT {select_list} FROM {_quote(src)}")
            self._con.unregister(src)
        # Lock it down. From here on nothing can touch the file system or change settings.
        self._con.execute("SET enable_external_access = false")
        self._con.execute("SET lock_configuration = true")

    # ------------------------------------------------------------------ #
    def validate(self, sql: str) -> str:
        """Return a cleaned single SELECT statement or raise QueryError."""
        cleaned = clean_sql(sql)
        if not cleaned:
            raise QueryError("The query is empty.", "validation")
        if len(cleaned) > MAX_SQL_CHARS:
            raise QueryError("The query is too long.", "validation")
        cur = self._con.cursor()
        try:
            statements = cur.extract_statements(cleaned)
        except duckdb.Error as exc:  # syntax error
            raise QueryError(_short_error(exc), "execution") from exc
        finally:
            cur.close()
        if len(statements) != 1:
            raise QueryError("Only a single SQL statement is allowed.", "validation")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise QueryError(
                f"Only read-only SELECT queries are allowed (got {statements[0].type.name}).", "validation"
            )
        return cleaned

    def execute(self, sql: str) -> QueryResult:
        cleaned = self.validate(sql)
        cur = self._con.cursor()
        timer = threading.Timer(self.timeout_seconds, cur.interrupt)
        start = time.perf_counter()
        timer.start()
        try:
            rel = cur.sql(cleaned)
            if rel is None:
                raise QueryError("The statement did not return a result set.", "validation")
            df = rel.limit(self.max_rows + 1).df()
        except duckdb.InterruptException as exc:
            raise QueryError(
                f"The query took longer than {self.timeout_seconds:.0f}s and was stopped.", "timeout"
            ) from exc
        except duckdb.PermissionException as exc:
            raise QueryError("The query tried to access files or settings, which is blocked in this sandbox.",
                             "validation") from exc
        except duckdb.Error as exc:
            raise QueryError(_short_error(exc), "execution") from exc
        finally:
            timer.cancel()
            cur.close()
        elapsed = (time.perf_counter() - start) * 1000
        truncated = len(df) > self.max_rows
        if truncated:
            df = df.head(self.max_rows)
        df.columns = _unique_columns([str(c) for c in df.columns])
        df = _normalise_dtypes(df)
        return QueryResult(sql=cleaned, df=df.reset_index(drop=True), truncated=truncated, elapsed_ms=elapsed)

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass


def _normalise_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """DECIMAL/HUGEINT can arrive as Python objects; make them floats so charts treat them as numbers."""
    for c in df.columns:
        if df[c].dtype == object:
            non_null = df[c].dropna()
            if len(non_null) and all(isinstance(v, (Decimal, int, float)) and not isinstance(v, bool)
                                     for v in non_null.head(50)):
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df


def _short_error(exc: Exception) -> str:
    msg = str(exc).strip()
    return msg if len(msg) < 1_500 else msg[:1_500] + " ..."


def _unique_columns(cols: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for c in cols:
        if c in seen:
            seen[c] += 1
            out.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            out.append(c)
    return out
