"""Deterministic (no-LLM) inference from the column profile.

Used in three ways: as hints inside the LLM prompt, as the fallback semantic
model when no LLM is configured or it fails, and in tests.
"""

from __future__ import annotations

import re

from ..engine.profile import ColumnProfile, TableProfile
from ..spec.models import ColumnSemantics, SemanticModel

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "finance": ["budget", "actual", "variance", "cost center", "cost_center", "costcenter", "gl", "ledger", "expense",
                "opex", "capex", "forecast", "fiscal", "invoice", "account", "spend", "revenue", "profit", "margin",
                "cash", "accrual", "amount"],
    "sales": ["order", "customer", "product", "sku", "channel", "sales", "discount", "unit price", "quantity", "qty",
              "deal", "pipeline", "opportunity", "lead", "region", "store"],
    "hr": ["employee", "headcount", "salary", "hire", "termination", "attrition", "leave", "department", "manager",
           "grade", "tenure", "gender", "nationality", "position", "job title", "fte"],
    "projects": ["project", "task", "milestone", "start date", "end date", "due", "owner", "sprint", "status", "phase",
                 "priority", "assignee", "completion"],
    "operations": ["ticket", "incident", "sla", "site", "asset", "shipment", "inventory", "stock", "supplier",
                   "warehouse", "downtime", "maintenance", "work order"],
    "marketing": ["campaign", "impressions", "clicks", "ctr", "conversion", "spend", "channel", "leads", "cpc"],
}

_ID_WORDS = re.compile(r"(^|[\s_])(id|ids|code|key|ref|reference|number|no|num|uuid|guid)([\s_]|$)|_id$|id$", re.I)
_CURRENCY_WORDS = re.compile(r"amount|cost|revenue|budget|actual|price|sales|spend|salary|value|total|fee|income|"
                             r"expense|profit|margin|payment|invoice|usd|aed|sar|eur|gbp|\$|€|£", re.I)
_CURRENCY_CODES = re.compile(r"(?<![A-Za-z])(USD|AED|SAR|EUR|GBP|INR|QAR|KWD|OMR|BHD|EGP|JPY)(?![A-Za-z])", re.I)
_PERCENT_WORDS = re.compile(r"pct|percent|%|rate|ratio|share|utili[sz]ation|attainment", re.I)
_COUNT_WORDS = re.compile(r"count|headcount|qty|quantity|units|number of|num_|#|volume|orders|tickets|items", re.I)
_SCORE_WORDS = re.compile(r"score|rating|nps|index", re.I)
_AVERAGE_WORDS = re.compile(r"price|rate|salary|wage|per[\s_]|avg|average|unit", re.I)
_YEAR_WORDS = re.compile(r"\byear\b|\bfy\b|fiscal_year|fiscalyear", re.I)
_STATUS_WORDS = re.compile(r"status|state|stage|phase|flag", re.I)
_GEO_WORDS = re.compile(r"country|city|region|location|site|branch|emirate|province|territory|market", re.I)
_NAME_WORDS = re.compile(r"name|title|description|comment|notes|remark", re.I)


def guess_currency(profile: TableProfile) -> str:
    votes: dict[str, int] = {}
    for c in profile.columns:
        for m in _CURRENCY_CODES.findall(c.name):
            votes[m.upper()] = votes.get(m.upper(), 0) + 2
        if c.data_type == "text":
            for v in c.top_values[:5]:
                for m in _CURRENCY_CODES.findall(v.value):
                    votes[m.upper()] = votes.get(m.upper(), 0) + 1
    return max(votes, key=votes.get) if votes else ""


def guess_domain(profile: TableProfile) -> tuple[str, list[str]]:
    names = " | ".join(c.name.lower() for c in profile.columns)
    best, best_hits = "", []
    for domain, words in DOMAIN_KEYWORDS.items():
        hits = [w for w in words if w in names]
        if len(hits) > len(best_hits):
            best, best_hits = domain, hits
    return (best if len(best_hits) >= 2 else "general"), best_hits


def guess_column(c: ColumnProfile, currency: str = "") -> ColumnSemantics:
    name = c.name
    sem = ColumnSemantics(name=name, data_type=c.data_type)  # type: ignore[arg-type]
    if c.data_type in ("date", "datetime"):
        sem.role, sem.semantic_type, sem.aggregation = "time", "date", "min"
        return sem
    if c.data_type == "boolean":
        sem.role, sem.semantic_type, sem.aggregation = "dimension", "boolean", "count"
        return sem
    if c.data_type in ("integer", "number"):
        is_int = c.numeric.is_integer if c.numeric else c.data_type == "integer"
        vals_look_like_years = (c.numeric is not None and c.numeric.min is not None and c.numeric.max is not None
                                and 1990 <= c.numeric.min and c.numeric.max <= 2100 and is_int and c.distinct <= 60)
        if _YEAR_WORDS.search(name) or (vals_look_like_years and c.distinct <= 30):
            sem.role, sem.semantic_type, sem.aggregation = "dimension", "year", "count"
            return sem
        if _ID_WORDS.search(name) and c.unique_ratio > 0.9:
            sem.role, sem.semantic_type, sem.aggregation = "identifier", "id", "count_distinct"
            return sem
        if _PERCENT_WORDS.search(name):
            sem.role, sem.semantic_type, sem.unit, sem.aggregation = "measure", "percent", "%", "mean"
            return sem
        if _CURRENCY_WORDS.search(name):
            code = _CURRENCY_CODES.search(name)
            sem.role, sem.semantic_type = "measure", "currency"
            sem.aggregation = "mean" if _AVERAGE_WORDS.search(name) else "sum"
            sem.unit = (code.group(1).upper() if code else "") or currency
            return sem
        if _SCORE_WORDS.search(name):
            sem.role, sem.semantic_type, sem.aggregation = "measure", "score", "mean"
            return sem
        if _COUNT_WORDS.search(name):
            sem.role, sem.semantic_type, sem.aggregation = "measure", "count", "sum"
            return sem
        if is_int and c.distinct <= 12 and c.non_null >= 30 and not _COUNT_WORDS.search(name):
            sem.role, sem.semantic_type, sem.aggregation = "dimension", "category", "count"  # e.g. quarter 1-4
            return sem
        sem.role, sem.semantic_type, sem.aggregation = "measure", "quantity", "sum"
        return sem
    # text
    if c.unique_ratio > 0.95 and c.distinct > 20:
        if _ID_WORDS.search(name) or c.avg_length <= 12:
            sem.role, sem.semantic_type, sem.aggregation = "identifier", "id", "count_distinct"
        elif c.avg_length > 40:
            sem.role, sem.semantic_type, sem.aggregation = "attribute", "text", "count"
        else:
            sem.role, sem.semantic_type, sem.aggregation = "attribute", "name", "count_distinct"
        return sem
    if c.distinct <= 50 or c.unique_ratio < 0.5:
        sem.role, sem.aggregation = "dimension", "count"
        if _STATUS_WORDS.search(name):
            sem.semantic_type = "status"
        elif _GEO_WORDS.search(name):
            sem.semantic_type = "geo"
        elif re.search(r"quarter|month|period|week", name, re.I):
            sem.semantic_type = "period"
        else:
            sem.semantic_type = "category"
        return sem
    sem.role, sem.semantic_type, sem.aggregation = "attribute", "name" if _NAME_WORDS.search(name) else "text", "count"
    return sem


def guess_data_shape(profile: TableProfile, columns: list[ColumnSemantics]) -> tuple[str, str]:
    ids = [c for c in columns if c.role == "identifier"]
    dims = [c for c in columns if c.role == "dimension"]
    n = profile.row_count
    if n >= 5_000 and ids:
        return "row_level", (f"{n:,} rows with a unique identifier column ('{ids[0].name}') - this looks like "
                             "transaction/record-level data that should be aggregated for charts.")
    if n >= 20_000:
        return "row_level", f"{n:,} rows - too many for a pre-aggregated summary; charts will aggregate."
    if n <= 500 and not ids and dims:
        return "aggregated", (f"Only {n:,} rows with categorical columns and no identifier - this looks like an "
                              "already summarised table.")
    if ids:
        return "row_level", f"Has an identifier column ('{ids[0].name}'), so each row is probably one record."
    return "unclear", f"{n:,} rows; could be either record-level or summarised data."


def heuristic_semantic_model(profile: TableProfile) -> SemanticModel:
    currency = guess_currency(profile)
    columns = [guess_column(c, currency) for c in profile.columns]
    domain, hits = guess_domain(profile)
    shape, reasoning = guess_data_shape(profile, columns)
    times = [c for c in columns if c.role == "time"]
    title = profile.file_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip().title() or "Dataset"
    if profile.sheet and profile.sheet.lower() not in {"sheet1", "data", "sheet"}:
        title = f"{title} - {profile.sheet}"
    desc = f"{profile.row_count:,} rows x {profile.column_count} columns."
    if hits:
        desc += f" Column names suggest {domain} data ({', '.join(hits[:5])})."
    return SemanticModel(title=title, description=desc, domain=domain, grain="", data_shape=shape,  # type: ignore[arg-type]
                         data_shape_reasoning=reasoning, time_column=times[0].name if times else "",
                         currency=currency, columns=columns, confidence="low",
                         open_questions=["Please check the column descriptions and types above - they were guessed "
                                         "from the data without an AI model."])


def hints_text(profile: TableProfile) -> str:
    """Compact rendering of the heuristic guesses, included in the LLM prompt as a starting point."""
    model = heuristic_semantic_model(profile)
    lines = [f"domain_guess: {model.domain}", f"currency_guess: {model.currency or 'none'}",
             f"data_shape_guess: {model.data_shape} ({model.data_shape_reasoning})"]
    for c in model.columns:
        lines.append(f"- {c.name}: role={c.role}, semantic_type={c.semantic_type}" + (f", unit={c.unit}" if c.unit else ""))
    return "\n".join(lines)
