"""Datasets, tables and their semantic layer (column definitions).

A *dataset* is a small "database": one or more tables, each with typed columns
and a plain-English description of what every column means. Built-in datasets
live in ``data/<id>/dataset.yaml`` next to their CSV files; users can create
their own by uploading CSVs in the app.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd
import yaml

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

COLUMN_TYPES = ["text", "integer", "decimal", "date", "datetime", "boolean"]
DUCKDB_TYPES = {
    "text": "VARCHAR",
    "integer": "BIGINT",
    "decimal": "DOUBLE",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "boolean": "BOOLEAN",
}
_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
@dataclass
class Column:
    name: str
    type: str
    description: str = ""


@dataclass
class Table:
    name: str
    description: str
    columns: list[Column]
    df: pd.DataFrame = field(repr=False)

    @property
    def row_count(self) -> int:
        return len(self.df)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class Dataset:
    id: str
    name: str
    description: str
    tables: list[Table]
    icon: str = "🗂️"
    as_of_date: str = ""
    currency: str = "USD"
    business_definitions: list[str] = field(default_factory=list)
    example_questions: list[str] = field(default_factory=list)
    source: str = "builtin"  # "builtin" | "uploaded"

    @property
    def label(self) -> str:
        return f"{self.icon} {self.name}"

    @property
    def fingerprint(self) -> str:
        """Stable hash of the schema + data shape, used as a cache key."""
        h = hashlib.sha256(self.id.encode())
        for t in self.tables:
            h.update(t.name.encode())
            h.update(str(t.df.shape).encode())
            for c in t.columns:
                h.update(f"{c.name}:{c.type}".encode())
            h.update(pd.util.hash_pandas_object(t.df.head(200), index=False).values.tobytes())
        return h.hexdigest()[:16]

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)

    def to_dictionary_yaml(self) -> str:
        """Export the semantic layer (no data) as YAML."""
        doc = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "as_of_date": self.as_of_date or None,
            "currency": self.currency,
            "tables": [
                {
                    "name": t.name,
                    "description": t.description,
                    "columns": [{"name": c.name, "type": c.type, "description": c.description} for c in t.columns],
                }
                for t in self.tables
            ],
            "business_definitions": self.business_definitions,
            "example_questions": self.example_questions,
        }
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


# --------------------------------------------------------------------------- #
# Type coercion
# --------------------------------------------------------------------------- #
def coerce_series(s: pd.Series, col_type: str) -> tuple[pd.Series, int]:
    """Convert a series to the declared type. Returns (series, n_values_that_failed)."""
    non_null_before = s.notna() & (s.astype(str).str.strip() != "")
    if col_type == "text":
        out = s.astype("string")
        out = out.where(out.str.strip() != "", pd.NA)
        return out, 0
    if col_type in ("integer", "decimal"):
        raw = s
        if raw.dtype == object or pd.api.types.is_string_dtype(raw):
            raw = (raw.astype("string").str.strip()
                   .str.replace(r"[,\s$€£%]", "", regex=True)
                   .str.replace(r"^\((.*)\)$", r"-\1", regex=True))  # (123) -> -123
        out = pd.to_numeric(raw, errors="coerce")
        if col_type == "integer":
            fractional = out.notna() & (out.round() != out)
            if fractional.any():  # don't silently truncate decimals
                out = out.astype("float64")
            else:
                out = out.round().astype("Int64")
        else:
            out = out.astype("float64")
    elif col_type in ("date", "datetime"):
        if pd.api.types.is_datetime64_any_dtype(s):
            out = s
        else:
            out = _parse_dates(s.astype("string"))
        if col_type == "date":
            out = out.dt.normalize()
    elif col_type == "boolean":
        lowered = s.astype("string").str.strip().str.lower()
        out = pd.Series(pd.NA, index=s.index, dtype="boolean")
        out[lowered.isin(_TRUE)] = True
        out[lowered.isin(_FALSE)] = False
    else:
        raise ValueError(f"Unknown column type: {col_type}")
    failed = int((non_null_before & out.isna()).sum())
    return out, failed


def _detect_dayfirst(s: pd.Series) -> list[bool]:
    """Look at a/b/yyyy-style values: a first part > 12 means day-first, a second part > 12 month-first."""
    parts = s.dropna().astype(str).str.extract(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}")
    parts = parts.dropna().astype(int)
    if parts.empty:
        return [False, True]
    if (parts[0] > 12).any() and not (parts[1] > 12).any():
        return [True]
    if (parts[1] > 12).any() and not (parts[0] > 12).any():
        return [False]
    return [False, True]


def _parse_dates(s: pd.Series) -> pd.Series:
    """Parse dates trying ISO first, then day-first and month-first formats."""
    best = pd.to_datetime(s, errors="coerce", format="ISO8601")
    if best.notna().mean() > 0.95:
        return best
    for dayfirst in _detect_dayfirst(s):
        try:
            cand = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst, format="mixed")
        except (ValueError, TypeError):
            continue
        if cand.notna().sum() > best.notna().sum():
            best = cand
    return best


def coerce_frame(df: pd.DataFrame, columns: list[Column]) -> tuple[pd.DataFrame, dict[str, int]]:
    out = pd.DataFrame(index=df.index)
    failures: dict[str, int] = {}
    for col in columns:
        out[col.name], failed = coerce_series(df[col.name], col.type)
        if failed:
            failures[col.name] = failed
    return out.reset_index(drop=True), failures


def infer_column_type(s: pd.Series) -> str:
    """Best-effort type guess for an uploaded column."""
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        non_null = s.dropna()
        if len(non_null) and (non_null.round() == non_null).all() and non_null.abs().max() < 2**53:
            return "integer"
        return "decimal"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date" if (s.dropna().dt.normalize() == s.dropna()).all() else "datetime"

    sample = s.dropna().astype(str).str.strip()
    sample = sample[sample != ""].head(2_000)
    if sample.empty:
        return "text"
    lowered = sample.str.lower()
    if lowered.isin(_TRUE | _FALSE).all() and lowered.nunique() <= 2:
        return "boolean"
    numeric = pd.to_numeric(sample.str.replace(r"[,\s$€£%]", "", regex=True), errors="coerce")
    if numeric.notna().mean() > 0.97:
        return "integer" if (numeric.dropna().round() == numeric.dropna()).all() else "decimal"
    looks_like_date = sample.str.contains(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2} [A-Za-z]{3}", regex=True).mean() > 0.9
    if looks_like_date:
        parsed = _parse_dates(sample)
        if parsed.notna().mean() > 0.95:
            has_time = (parsed.dropna().dt.normalize() != parsed.dropna()).any()
            return "datetime" if has_time else "date"
    return "text"


# --------------------------------------------------------------------------- #
# Identifier hygiene for uploaded data
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _reserved_words() -> frozenset[str]:
    try:
        rows = duckdb.sql("SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category = 'reserved'").fetchall()
        return frozenset(r[0].lower() for r in rows)
    except Exception:
        return frozenset({"select", "from", "where", "group", "order", "by", "limit", "table", "join", "on",
                          "as", "and", "or", "not", "null", "case", "when", "then", "end", "union", "all"})


def sanitize_identifier(name: str, fallback: str = "column") -> str:
    """Turn any header into a safe snake_case SQL identifier."""
    s = str(name).strip().replace("%", " pct ").replace("#", " num ").replace("&", " and ")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()
    s = re.sub(r"_+", "_", s)
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"{fallback}_{s}" if fallback == "table" else f"c_{s}"
    if s in _reserved_words():
        s = f"{s}_value" if fallback != "table" else f"{s}_table"
    return s[:63]


def is_valid_identifier(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name or "")) and name not in _reserved_words()


def dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
        else:
            seen[n] = 1
            out.append(n)
    return out


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_dataset_dir(path: Path) -> Dataset:
    meta = yaml.safe_load((path / "dataset.yaml").read_text(encoding="utf-8"))
    tables = []
    for t in meta["tables"]:
        columns = [Column(c["name"], c["type"], " ".join(str(c.get("description", "")).split()))
                   for c in t["columns"]]
        raw = pd.read_csv(path / t["file"], dtype=str, keep_default_na=True)
        missing = [c.name for c in columns if c.name not in raw.columns]
        if missing:
            raise ValueError(f"{path.name}/{t['file']}: columns missing from CSV: {missing}")
        df, _ = coerce_frame(raw, columns)
        tables.append(Table(t["name"], " ".join(str(t.get("description", "")).split()), columns, df))
    return Dataset(
        id=meta["id"],
        name=meta["name"],
        description=" ".join(str(meta.get("description", "")).split()),
        tables=tables,
        icon=meta.get("icon", "🗂️"),
        as_of_date=str(meta.get("as_of_date", "") or ""),
        currency=meta.get("currency", "USD"),
        business_definitions=list(meta.get("business_definitions", []) or []),
        example_questions=list(meta.get("example_questions", []) or []),
        source="builtin",
    )


def load_builtin_datasets(data_dir: Path = DATA_DIR) -> dict[str, Dataset]:
    order = ["finance", "hr", "projects"]
    dirs = sorted((p for p in data_dir.iterdir() if (p / "dataset.yaml").exists()),
                  key=lambda p: (order.index(p.name) if p.name in order else 99, p.name))
    datasets = {}
    for d in dirs:
        ds = load_dataset_dir(d)
        datasets[ds.id] = ds
    return datasets


def read_uploaded_csv(content: bytes, max_rows: int = 500_000) -> pd.DataFrame:
    """Read an uploaded delimited text file robustly (encoding + delimiter sniffing)."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None or not text.strip():
        raise ValueError("The file is empty or not a text/CSV file.")
    sample = text[:20_000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    df = pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, keep_default_na=True,
                     nrows=max_rows + 1, skipinitialspace=True, on_bad_lines="warn")
    if len(df) > max_rows:
        raise ValueError(f"The file has more than {max_rows:,} rows. Please upload a smaller extract.")
    if df.shape[1] < 1 or df.empty:
        raise ValueError("No rows found. Check that the first line contains column headers.")
    df = df.dropna(how="all")
    df.columns = [str(c) for c in df.columns]
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Profiling: compact facts about each column that help the LLM write good SQL
# --------------------------------------------------------------------------- #
def _fmt_num(x: float) -> str:
    if pd.isna(x):
        return "n/a"
    if abs(x) >= 1000 or float(x).is_integer():
        return f"{x:,.0f}"
    return f"{x:,.4g}"


def profile_column(s: pd.Series, col_type: str) -> str:
    n = len(s)
    nulls = int(s.isna().sum())
    parts = []
    non_null = s.dropna()
    if col_type in ("integer", "decimal") and len(non_null):
        parts.append(f"range {_fmt_num(non_null.min())} to {_fmt_num(non_null.max())}")
    elif col_type in ("date", "datetime") and len(non_null):
        fmt = "%Y-%m-%d" if col_type == "date" else "%Y-%m-%d %H:%M"
        parts.append(f"range {non_null.min():{fmt}} to {non_null.max():{fmt}}")
    elif col_type == "boolean":
        parts.append("true/false")
    elif col_type == "text" and len(non_null):
        uniq = non_null.astype(str).unique()
        is_key = str(s.name).endswith("_id") and len(uniq) == len(non_null)
        if is_key and len(uniq) > 5:
            parts.append(f"unique key, e.g. '{uniq[0]}', '{uniq[1]}'")
        elif len(uniq) <= 30:
            vals = ", ".join(f"'{v}'" for v in sorted(uniq)[:30])
            parts.append(f"values: {vals}")
        else:
            examples = ", ".join(f"'{v}'" for v in non_null.astype(str).drop_duplicates().head(3))
            parts.append(f"{len(uniq):,} distinct, e.g. {examples}")
    if nulls:
        parts.append(f"{nulls / n:.0%} NULL" if nulls != n else "all NULL")
    return "; ".join(parts)


def schema_for_prompt(ds: Dataset) -> str:
    """Render the dataset's schema + semantic layer as compact text for the LLM."""
    lines = [f"Dataset: {ds.name}", f"About: {ds.description}"]
    if ds.as_of_date:
        lines.append(f"Data is current as of {ds.as_of_date}; treat that date as 'today'.")
    if ds.currency:
        lines.append(f"Currency: {ds.currency}")
    for t in ds.tables:
        lines.append("")
        lines.append(f"TABLE {t.name}  ({t.row_count:,} rows) - {t.description}")
        for c in t.columns:
            facts = profile_column(t.df[c.name], c.type)
            desc = c.description or "(no description)"
            lines.append(f"  - {c.name} {DUCKDB_TYPES[c.type]}: {desc}" + (f" [{facts}]" if facts else ""))
    if ds.business_definitions:
        lines.append("")
        lines.append("BUSINESS DEFINITIONS (follow these exactly):")
        lines.extend(f"  - {d}" for d in ds.business_definitions)
    return "\n".join(lines)


def table_preview_for_prompt(df: pd.DataFrame, n: int = 5) -> str:
    sample = df.head(n).copy()
    for c in sample.columns:
        if pd.api.types.is_datetime64_any_dtype(sample[c]):
            sample[c] = sample[c].dt.strftime("%Y-%m-%d")
    return sample.to_csv(index=False)


def to_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of a frame that renders nicely (dates without 00:00:00)."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            col = out[c].dropna()
            if len(col) and (col.dt.normalize() == col).all():
                out[c] = out[c].dt.date
    return out


def make_dataset_id(name: str) -> str:
    return "upload_" + (sanitize_identifier(name, "dataset") or "dataset")[:40]


__all__ = [
    "COLUMN_TYPES", "Column", "Dataset", "Table", "coerce_frame", "coerce_series", "dedupe",
    "infer_column_type", "is_valid_identifier", "load_builtin_datasets", "load_dataset_dir",
    "make_dataset_id", "profile_column", "read_uploaded_csv", "sanitize_identifier",
    "schema_for_prompt", "table_preview_for_prompt", "to_display_frame",
]

