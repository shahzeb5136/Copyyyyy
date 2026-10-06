"""Turning a chat session into a downloadable report."""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from core import catalog, pipeline, report
from core.database import QueryEngine
from core.llm import LLMError
from core.offline_llm import OfflineLLM
from tests.test_pipeline import ScriptedLLM, _sql

NOW = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)


def _ask(question, ds, engine, llm, messages):
    """A trimmed-down version of what the Ask page stores per question."""
    out = pipeline.run_sql_stage(question, ds, engine, llm)
    messages.append({"role": "user", "content": question})
    if not out.ok:
        kind = "cannot_answer" if not out.can_answer else "error"
        messages.append({"role": "assistant", "kind": kind, "answer": out.assumptions or out.error, "sql": out.sql})
        return
    viz = pipeline.design_charts(out, ds, llm)
    answer = "".join(pipeline.stream_answer(out, ds, llm))
    messages.append({"role": "assistant", "kind": "answer", "answer": answer, "sql": out.sql,
                     "assumptions": out.assumptions, "df": out.result.df, "truncated": out.result.truncated,
                     "charts": [s.to_dict() for s in viz.specs]})


@pytest.fixture
def finance_session(datasets, engines):
    ds = datasets["finance"]
    llm = OfflineLLM(latency=0)
    messages: list[dict] = []
    for q in ds.example_questions[:2]:
        _ask(q, ds, engines["finance"], llm, messages)
    _ask("What's the weather on Mars?", ds, engines["finance"], llm, messages)
    return ds, messages


def _narrative(numbers, **overrides):
    data = {
        "title": "Budget performance review",
        "executive_summary": "Spend ran **2.1% over** budget.",
        "key_findings": ["IT overspent by **USD 1.2M**. (Q1)"],
        "sections": [{"question_number": n, "headline": f"Headline {n}", "commentary": f"Commentary {n}."}
                     for n in numbers],
        "next_steps": ["Break IT spend down by category."],
        "caveats": "",
    }
    data.update(overrides)
    return data


def test_collect_items_keeps_order_and_numbers(finance_session):
    ds, messages = finance_session
    items = report.collect_items(messages)
    assert [it.number for it in items] == [1, 2, 3]
    assert [it.answered for it in items] == [True, True, False]
    assert items[0].question == ds.example_questions[0]
    assert items[2].kind == "cannot_answer"


def test_offline_report_is_complete(finance_session):
    ds, messages = finance_session
    rep = report.build_report(messages, ds, OfflineLLM(latency=0), now=NOW)
    doc = rep.html
    assert rep.narrative.source == "basic" and "Offline demo mode" in rep.narrative.note
    assert rep.answered_count == 2 and rep.question_count == 3
    assert doc.startswith("<!doctype html>") and "Plotly.newPlot" in doc
    # sections follow the order the questions were asked; SQL and data tables are included
    first, second = (doc.index(f'id="q{n}"') for n in (1, 2))
    assert first < second
    assert "SELECT" in doc and "<table>" in doc
    assert "Questions not answered" in doc and "weather on Mars" in doc
    assert rep.filename.endswith(".html") and "20261001_0930" in rep.filename


def test_ai_narrative_is_used_and_gaps_are_filled(finance_session):
    ds, messages = finance_session
    # Q2 is missing from the model's sections and Q9 does not exist -> Q2 falls back, Q9 is ignored.
    sections = [{"question_number": 1, "headline": "IT is over budget", "commentary": "Biggest gap."},
                {"question_number": 9, "headline": "Ghost", "commentary": "Nope."}]
    llm = ScriptedLLM([_narrative([], sections=sections)])
    rep = report.build_report(messages, ds, llm, model_label="Test model", now=NOW)
    n = rep.narrative
    assert n.source == "ai" and n.title == "Budget performance review"
    assert n.sections[1] == ("IT is over budget", "Biggest gap.")
    assert n.sections[2][0]  # filled from the session
    assert 9 not in n.sections
    assert "Ghost" not in rep.html and "IT is over budget" in rep.html
    assert "<strong>2.1% over</strong>" in rep.html and "Narrative by" in rep.html
    prompt = llm.prompts[0]
    assert "### Q1:" in prompt and "### Q2:" in prompt and "rows_returned" in prompt
    assert "Q3: What's the weather on Mars? (outside what the dataset covers)" in prompt


def test_llm_failure_falls_back_to_basic_report(finance_session):
    ds, messages = finance_session
    rep = report.build_report(messages, ds, ScriptedLLM([LLMError("rate limited")]), now=NOW)
    assert rep.narrative.source == "basic" and "rate limited" in rep.narrative.note
    assert len(rep.narrative.key_findings) == 2


def test_same_session_gives_same_report(finance_session):
    ds, messages = finance_session
    a = report.build_report(messages, ds, ScriptedLLM([_narrative([1, 2])]), now=NOW)
    b = report.build_report(messages, ds, ScriptedLLM([_narrative([1, 2])]), now=NOW)
    assert a.fingerprint == b.fingerprint and a.html == b.html


def test_fingerprint_changes_with_a_new_question(finance_session, engines):
    ds, messages = finance_session
    before = report.fingerprint(report.collect_items(messages), ds)
    _ask(ds.example_questions[2], ds, engines["finance"], OfflineLLM(latency=0), messages)
    assert report.fingerprint(report.collect_items(messages), ds) != before


def test_requires_an_answered_question(datasets):
    messages = [{"role": "user", "content": "weather?"},
                {"role": "assistant", "kind": "cannot_answer", "answer": "No."}]
    with pytest.raises(ValueError):
        report.build_report(messages, datasets["finance"], None)


def test_markdown_is_escaped():
    out = report.md_to_html("Total is **$5M** <script>alert(1)</script>\n\n- one\n- *two*")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<strong>$5M</strong>" in out and "<ul><li>one</li><li><em>two</em></li></ul>" in out
    assert "net_sales_aed" in report.md_to_html("net_sales_aed")  # snake_case is not italicised


def test_report_on_uploaded_csv():
    sample = Path(catalog.DATA_DIR, "samples", "online_store_orders.csv").read_bytes()
    raw = catalog.read_uploaded_csv(sample)
    names = catalog.dedupe([catalog.sanitize_identifier(c) for c in raw.columns])
    raw.columns = names
    columns = [catalog.Column(c, catalog.infer_column_type(raw[c]), "") for c in names]
    df, _ = catalog.coerce_frame(raw, columns)
    ds = catalog.Dataset(id="upload_orders", name="Online orders", description="Uploaded.", source="uploaded",
                         tables=[catalog.Table("orders", "Orders.", columns, df)])
    engine = QueryEngine(ds, max_rows=1_000, timeout_seconds=5)
    messages: list[dict] = []
    chart = {"chart_type": "kpi", "title": "Order count", "y": ["n_orders"]}
    llm = ScriptedLLM([_sql("SELECT COUNT(*) AS n_orders FROM orders"), {"reasoning": "", "charts": [chart]}])
    _ask("How many orders?", ds, engine, llm, messages)
    rep = report.build_report(messages, ds, None, now=NOW)
    assert rep.narrative.source == "basic" and "No AI model" in rep.narrative.note
    assert "How many orders?" in rep.html and 'class="kpi"' in rep.html
    assert isinstance(messages[1]["df"], pd.DataFrame)
