"""Compile a chat session into a downloadable, self-contained HTML report.

The report is built from what the session already produced - every question,
the analyst's answer, the result data, the chart specs and the SQL - so the
facts in it are exactly the ones the user saw. One extra LLM call (the *report
agent*) writes the narrative around them: a title, an executive summary, key
findings and a headline + commentary per question.

To keep reports predictable:

* Structure is fixed by software: sections follow the order the questions were
  asked, and charts, tables and SQL are rendered from the stored results.
* The LLM only fills a strict JSON schema; anything missing falls back to
  deterministic text derived from the answers.
* Each report carries a fingerprint of the conversation it was built from, so the
  UI can reuse it until a new question is asked instead of regenerating it.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
from plotly.offline import get_plotlyjs

from . import charts as chartlib
from . import prompts
from .catalog import Dataset, sanitize_identifier, to_display_frame
from .charts import ChartSpec, is_numeric
from .database import QueryResult
from .llm import BaseLLM, LLMError, Usage
from .pipeline import result_for_analyst

MAX_TABLE_ROWS = 20
MAX_PROMPT_ROWS = 25


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
@dataclass
class ReportItem:
    """One question of the session and what it produced."""

    number: int
    question: str
    kind: str  # "answer" | "cannot_answer" | "error"
    answer: str = ""
    assumptions: str = ""
    sql: str = ""
    df: pd.DataFrame | None = None
    truncated: bool = False
    charts: list[dict] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.kind == "answer" and self.df is not None


@dataclass
class ReportNarrative:
    title: str
    executive_summary: str
    key_findings: list[str]
    sections: dict[int, tuple[str, str]]  # question number -> (headline, commentary)
    next_steps: list[str]
    caveats: str
    source: str = "ai"  # "ai" | "basic"
    note: str = ""  # why the basic narrative was used
    usage: Usage | None = None
    elapsed_ms: float = 0.0


@dataclass
class Report:
    html: str
    filename: str
    fingerprint: str
    title: str
    question_count: int
    answered_count: int
    narrative: ReportNarrative


# --------------------------------------------------------------------------- #
# Collecting the session
# --------------------------------------------------------------------------- #
def collect_items(messages: list[dict]) -> list[ReportItem]:
    """Pair each user question with the assistant reply that followed it, in order."""
    items: list[ReportItem] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        reply = messages[i + 1] if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant" else {}
        kind = reply.get("kind", "error") if reply else "error"
        if kind not in ("answer", "cannot_answer"):
            kind = "error"
        items.append(ReportItem(
            number=len(items) + 1,
            question=str(msg.get("content", "")).strip(),
            kind=kind,
            answer=str(reply.get("answer", "") or "").strip(),
            assumptions=str(reply.get("assumptions", "") or "").strip(),
            sql=str(reply.get("sql", "") or "").strip(),
            df=reply.get("df"),
            truncated=bool(reply.get("truncated")),
            charts=list(reply.get("charts") or []),
        ))
    return items


def fingerprint(items: list[ReportItem], dataset: Dataset) -> str:
    """Stable hash of everything the report is built from."""
    h = hashlib.sha256(dataset.fingerprint.encode())
    for it in items:
        h.update(json.dumps([it.number, it.question, it.kind, it.answer, it.assumptions, it.sql, it.truncated,
                             it.charts], sort_keys=True, default=str).encode())
        if it.df is not None:
            h.update(str(list(it.df.columns)).encode())
            h.update(pd.util.hash_pandas_object(it.df, index=False).values.tobytes())
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------- #
# The report agent
# --------------------------------------------------------------------------- #
def _dataset_text(ds: Dataset) -> str:
    lines = [f"Name: {ds.name}", f"About: {ds.description}", f"Tables: {', '.join(t.name for t in ds.tables)}"]
    if ds.as_of_date:
        lines.append(f"Data as of: {ds.as_of_date}")
    if ds.currency:
        lines.append(f"Currency: {ds.currency}")
    return "\n".join(lines)


def report_prompt(items: list[ReportItem], dataset: Dataset) -> str:
    answered, unanswered = [], []
    for it in items:
        if it.answered:
            block = [f"### Q{it.number}: {it.question}"]
            if it.assumptions:
                block.append(f"Assumptions: {it.assumptions}")
            block.append(f"Answer given:\n{it.answer or '(no written answer)'}")
            titles = [c.get("title", "") for c in it.charts if c.get("title")]
            if titles:
                block.append("Chart(s) shown: " + "; ".join(titles))
            result = QueryResult(sql=it.sql, df=it.df, truncated=it.truncated, elapsed_ms=0)
            block.append("Result data:\n" + result_for_analyst(result, max_rows=MAX_PROMPT_ROWS))
            answered.append("\n".join(block))
        else:
            reason = "outside what the dataset covers" if it.kind == "cannot_answer" else "failed"
            unanswered.append(f"Q{it.number}: {it.question} ({reason})")
    return prompts.report_user_prompt(_dataset_text(dataset), "\n\n".join(answered), "\n".join(unanswered))


def _strip_md(text: str) -> str:
    return re.sub(r"[*_`]", "", text or "").strip()


def _first_sentence(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not re.match(r"^([-*•]|\d+\.)\s", line):
            return re.split(r"(?<=[.!?])\s+", line, maxsplit=1)[0]
    return ""


def basic_narrative(items: list[ReportItem], dataset: Dataset, note: str = "",
                    prefer_chart_titles: bool = False) -> ReportNarrative:
    """Deterministic narrative built only from what the session produced (no LLM)."""
    answered = [it for it in items if it.answered]
    findings, sections = [], {}
    for it in answered:
        chart_title = next((c.get("title", "") for c in it.charts if c.get("title")), "")
        sentence = _first_sentence(it.answer)
        finding = (chart_title or sentence) if prefer_chart_titles else (sentence or chart_title)
        if not finding:
            finding = f"{it.question.rstrip('?')} returned {len(it.df):,} row(s)."
        if len(findings) < 6:
            findings.append(f"{finding.rstrip('.')}. (Q{it.number})")
        sections[it.number] = (chart_title or it.question, "")
    topics = "; ".join(f"Q{it.number} {it.question.rstrip('?')}" for it in answered[:4])
    summary = (f"This report compiles {len(answered)} answered question(s) from an analysis session on "
               f"{dataset.name}" + (f", with data as of {dataset.as_of_date}" if dataset.as_of_date else "")
               + f". It covers: {topics}" + ("; and more" if len(answered) > 4 else "") + ".")
    caveats = " ".join(f"Q{it.number}: {it.assumptions.rstrip('.')}." for it in answered if it.assumptions)
    if any(it.truncated for it in answered):
        caveats += " Some results were truncated to the row limit."
    return ReportNarrative(
        title=f"{dataset.name.split('·')[-1].strip()} - Analysis Report",
        executive_summary=summary,
        key_findings=findings,
        sections=sections,
        next_steps=[],
        caveats=caveats.strip(),
        source="basic",
        note=note,
    )


def draft_narrative(items: list[ReportItem], dataset: Dataset, llm: BaseLLM | None) -> ReportNarrative:
    """Ask the report agent for a narrative; fall back to the basic one on any problem."""
    if llm is None:
        return basic_narrative(items, dataset, "No AI model is configured, so this is an automatic summary.")
    if llm.provider == "offline":
        return basic_narrative(items, dataset, "Offline demo mode - this is an automatic summary, not AI-written.",
                               prefer_chart_titles=True)
    basic = basic_narrative(items, dataset)
    try:
        res = llm.generate_json(system=prompts.REPORT_SYSTEM, user=report_prompt(items, dataset),
                                schema=prompts.REPORT_SCHEMA, schema_name="chat_report", max_tokens=12_000)
    except LLMError as exc:
        basic.note = f"The report agent couldn't write a narrative ({exc}), so this is an automatic summary."
        return basic
    d = res.data
    valid = {it.number for it in items if it.answered}
    sections: dict[int, tuple[str, str]] = {}
    for s in d.get("sections", []) or []:
        if not isinstance(s, dict):
            continue
        try:
            n = int(s.get("question_number"))
        except (TypeError, ValueError):
            continue
        if n in valid and n not in sections:
            sections[n] = (str(s.get("headline", "")).strip(), str(s.get("commentary", "")).strip())
    for n, fallback in basic.sections.items():  # every answered question gets a section
        if not sections.get(n, ("", ""))[0]:
            sections[n] = (fallback[0], sections.get(n, ("", ""))[1])

    def _strings(key: str) -> list[str]:
        return [str(x).strip() for x in (d.get(key) or []) if str(x).strip()]

    return ReportNarrative(
        title=str(d.get("title", "")).strip() or basic.title,
        executive_summary=str(d.get("executive_summary", "")).strip() or basic.executive_summary,
        key_findings=_strings("key_findings") or basic.key_findings,
        sections=sections,
        next_steps=_strings("next_steps"),
        caveats=str(d.get("caveats", "")).strip(),
        source="ai",
        usage=res.usage,
        elapsed_ms=res.elapsed_ms,
    )


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #
def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _inline_md(text: str) -> str:
    s = _esc(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?![\s_])(.+?)(?<![\s_])_(?![\w_])", r"<em>\1</em>", s)
    return s


def md_to_html(text: str) -> str:
    """Tiny, safe Markdown subset (paragraphs, bullets, bold, italics) - everything else is escaped."""
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + _inline_md(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline_md(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for raw in (text or "").splitlines():
        line = raw.strip()
        bullet = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)$", line)
        if not line:
            flush()
        elif bullet:
            if para:
                out.append("<p>" + _inline_md(" ".join(para)) + "</p>")
                para.clear()
            items.append(bullet.group(1))
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "".join(out)


def _cell(value, column: str = "") -> str:
    if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if column.lower().endswith(("_pct", "_percent")):
            return f"{value:,.1f}%"
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    return str(value)


def _table_html(df: pd.DataFrame, truncated: bool) -> str:
    shown = to_display_frame(df.head(MAX_TABLE_ROWS))
    numeric = {c for c in df.columns if is_numeric(df[c])}
    head = "".join(f'<th class="{"num" if c in numeric else ""}">{_esc(chartlib.pretty(c))}</th>' for c in shown.columns)
    rows = []
    for rec in shown.itertuples(index=False):
        cells = "".join(f'<td class="{"num" if c in numeric else ""}">{_esc(_cell(v, c))}</td>'
                        for c, v in zip(shown.columns, rec))
        rows.append(f"<tr>{cells}</tr>")
    caption = f"{len(df):,} row(s)"
    if len(df) > MAX_TABLE_ROWS:
        caption = f"First {MAX_TABLE_ROWS} of {len(df):,} rows"
    if truncated:
        caption += " (result truncated to the row limit)"
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<div class="caption">{_esc(caption)}</div></div>')


def _charts_html(it: ReportItem, dataset: Dataset) -> tuple[str, bool]:
    """Render the item's charts. Returns (html, uses_plotly)."""
    if it.df is None or it.df.empty or not it.charts:
        return "", False
    specs = [ChartSpec.from_dict(c) for c in it.charts]
    rendered, _problems = chartlib.build_charts(specs, it.df, dataset.currency, dataset.as_of_date or None)
    parts, uses_plotly = [], False
    for i, ch in enumerate(rendered):
        body = ""
        if ch.kind == "kpi":
            body = '<div class="kpis">' + "".join(
                f'<div class="kpi"><div class="k">{_esc(k)}</div><div class="v">{_esc(v)}</div></div>'
                for k, v in ch.metrics) + "</div>"
        elif ch.figure is not None:
            uses_plotly = True
            body = ch.figure.to_html(full_html=False, include_plotlyjs=False, div_id=f"q{it.number}-chart-{i + 1}",
                                     default_width="100%",
                                     config={"displaylogo": False, "responsive": True})
        if body:
            title = f'<div class="chart-title">{_esc(ch.title)}</div>' if ch.title else ""
            note = f'<div class="caption">{_esc(ch.note)}</div>' if ch.note else ""
            parts.append(f'<figure class="chart">{title}{body}{note}</figure>')
    return "".join(parts), uses_plotly


REPORT_CSS = """
:root { --ink:#111827; --ink2:#374151; --muted:#6b7280; --border:#e5e7eb; --surface:#f6f8fb;
  --primary:#2a6fdb; --primary-soft:#eaf1fd; }
* { box-sizing: border-box; }
body { margin:0; background:#fff; color:var(--ink); font:15px/1.6 Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',
  Roboto,sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 40px 24px 64px; }
h1 { font-size: 2rem; line-height: 1.2; margin: 6px 0 10px; letter-spacing: -0.015em; }
h2 { font-size: 1.35rem; line-height: 1.3; margin: 4px 0 8px; letter-spacing: -0.01em; }
h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); margin: 28px 0 8px; }
p { margin: 0 0 10px; } ul, ol { margin: 0 0 10px; padding-left: 22px; } li { margin: 3px 0; }
a { color: var(--primary); }
.eyebrow { font-size: .78rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--primary); }
header { border: 1px solid var(--border); border-radius: 16px; padding: 24px 28px;
  background: radial-gradient(1200px 300px at 0% 0%, #eef4ff 0%, #ffffff 60%); }
.meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.chip { font-size: .8rem; color: var(--ink2); background: #fff; border: 1px solid var(--border); border-radius: 999px;
  padding: 2px 10px; } .chip b { color: var(--ink); }
.note { font-size: .82rem; color: var(--muted); margin-top: 10px; }
.summary { font-size: 1.02rem; color: var(--ink2); }
.findings li::marker { color: var(--primary); }
.toc { columns: 2; column-gap: 32px; font-size: .9rem; } .toc li { break-inside: avoid; }
section.q { border-top: 1px solid var(--border); padding-top: 26px; margin-top: 30px; }
.qnum { font-size: .75rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--primary); }
.asked { color: var(--muted); font-style: italic; margin-bottom: 12px; }
.commentary { color: var(--ink2); }
.answer { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 18px;
  margin: 14px 0; } .answer > :last-child { margin-bottom: 0; }
.label { font-size: .72rem; font-weight: 600; letter-spacing: .07em; text-transform: uppercase; color: var(--muted);
  margin-bottom: 6px; }
figure.chart { margin: 16px 0; break-inside: avoid; }
.chart-title { font-weight: 600; margin: 0 0 4px 2px; }
.kpis { display: flex; flex-wrap: wrap; gap: 10px; }
.kpi { flex: 1 1 160px; border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; }
.kpi .k { color: var(--muted); font-size: .82rem; } .kpi .v { font-size: 1.5rem; font-weight: 700; }
.table-wrap { overflow-x: auto; margin: 12px 0; break-inside: avoid; }
table { border-collapse: collapse; width: 100%; font-size: .84rem; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
th { background: var(--surface); font-weight: 600; color: var(--ink2); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.caption { color: var(--muted); font-size: .78rem; margin-top: 4px; }
.assumption { font-size: .85rem; color: var(--muted); }
.unanswered li span { color: var(--muted); }
pre { background: #0f172a; color: #e2e8f0; border-radius: 10px; padding: 12px 14px; font-size: .78rem;
  white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
footer { margin-top: 48px; color: var(--muted); font-size: .78rem; border-top: 1px solid var(--border); padding-top: 12px; }
@media print {
  main { padding: 0; max-width: none; } header { border: none; padding: 0; background: none; }
  section.q { break-before: page; border-top: none; } .toc { columns: 1; }
  a { color: inherit; text-decoration: none; }
}
@media (max-width: 640px) { .toc { columns: 1; } h1 { font-size: 1.6rem; } }
"""


def render_html(dataset: Dataset, items: list[ReportItem], narrative: ReportNarrative,
                generated_at: datetime, model_label: str = "") -> str:
    answered = [it for it in items if it.answered]
    unanswered = [it for it in items if not it.answered]

    chips = [("Dataset", dataset.name), ("Questions", f"{len(answered)} answered" +
                                         (f" of {len(items)}" if unanswered else ""))]
    if dataset.as_of_date:
        chips.append(("Data as of", dataset.as_of_date))
    chips.append(("Generated", generated_at.strftime("%d %b %Y, %H:%M UTC")))
    if narrative.source == "ai" and model_label:
        chips.append(("Narrative by", model_label))
    chips_html = "".join(f'<span class="chip">{_esc(k)} <b>{_esc(v)}</b></span>' for k, v in chips)
    note = f'<div class="note">{_esc(narrative.note)}</div>' if narrative.note else ""

    body = [f'<header><div class="eyebrow">Analysis report</div><h1>{_esc(narrative.title)}</h1>'
            f'<div class="meta">{chips_html}</div>{note}</header>']

    body.append(f'<h3>Executive summary</h3><div class="summary">{md_to_html(narrative.executive_summary)}</div>')
    if narrative.key_findings:
        body.append('<h3>Key findings</h3><ul class="findings">'
                    + "".join(f"<li>{_inline_md(f)}</li>" for f in narrative.key_findings) + "</ul>")
    toc = "".join(f'<li><a href="#q{it.number}">Q{it.number}. {_esc(narrative.sections.get(it.number, (it.question,))[0] or it.question)}</a></li>'
                  for it in answered)
    body.append(f'<h3>Contents</h3><ol class="toc">{toc}</ol>')

    uses_plotly = False
    for it in answered:
        headline, commentary = narrative.sections.get(it.number, ("", ""))
        charts_html, plotly_here = _charts_html(it, dataset)
        uses_plotly = uses_plotly or plotly_here
        parts = [f'<section class="q" id="q{it.number}"><div class="qnum">Question {it.number}</div>']
        if headline and headline != it.question:
            parts.append(f"<h2>{_esc(headline)}</h2><p class=\"asked\">“{_esc(it.question)}”</p>")
        else:
            parts.append(f"<h2>{_esc(it.question)}</h2>")
        if commentary:
            parts.append(f'<div class="commentary">{md_to_html(commentary)}</div>')
        if it.answer:
            parts.append(f'<div class="answer"><div class="label">Answer</div>{md_to_html(it.answer)}</div>')
        parts.append(charts_html)
        if it.df is not None and not it.df.empty:
            parts.append(_table_html(it.df, it.truncated))
        elif it.df is not None:
            parts.append('<p class="caption">The query ran successfully but returned no rows.</p>')
        if it.assumptions:
            parts.append(f'<p class="assumption"><b>Assumptions:</b> {_esc(it.assumptions)}</p>')
        parts.append("</section>")
        body.append("".join(parts))

    tail = []
    if unanswered:
        tail.append('<h3>Questions not answered</h3><ul class="unanswered">' + "".join(
            f"<li>Q{it.number}. {_esc(it.question)} <span>— "
            f"{_esc(_strip_md(it.answer) or ('Outside what this dataset covers.' if it.kind == 'cannot_answer' else 'The query failed.'))}"
            f"</span></li>" for it in unanswered) + "</ul>")
    if narrative.next_steps:
        tail.append("<h3>Suggested next steps</h3><ol>"
                    + "".join(f"<li>{_inline_md(s)}</li>" for s in narrative.next_steps) + "</ol>")
    if narrative.caveats:
        tail.append(f"<h3>Caveats</h3>{md_to_html(narrative.caveats)}")
    tail.append(
        "<h3>Appendix: how this report was produced</h3>"
        "<p>Each question was translated into SQL by an AI agent and run against the dataset in a sandboxed, "
        "read-only SQL engine. The answers, charts and tables above come directly from those query results. "
        + ("The title, summary, findings and commentary were written by an AI report agent from the same results."
           if narrative.source == "ai" else
           "The summary and findings were assembled automatically from the answers.")
        + " Review important figures before relying on them.</p>")
    for it in answered:
        if it.sql:
            tail.append(f"<p><b>Q{it.number}.</b> {_esc(it.question)}</p><pre><code>{_esc(it.sql)}</code></pre>")
    body.append(f'<section class="end">{"".join(tail)}</section>')
    body.append(f"<footer>{_esc(dataset.name)} · generated {_esc(generated_at.strftime('%Y-%m-%d %H:%M UTC'))}"
                + (f" · {_esc(dataset.description)}" if dataset.description else "") + "</footer>")

    script = f'<script type="text/javascript">{get_plotlyjs()}</script>' if uses_plotly else ""
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{_esc(narrative.title)}</title><style>{REPORT_CSS}</style>{script}</head>"
            f"<body><main>{''.join(body)}</main></body></html>")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_report(messages: list[dict], dataset: Dataset, llm: BaseLLM | None, model_label: str = "",
                 now: datetime | None = None) -> Report:
    items = collect_items(messages)
    answered = [it for it in items if it.answered]
    if not answered:
        raise ValueError("Ask at least one question that returns data before creating a report.")
    start = time.perf_counter()
    narrative = draft_narrative(items, dataset, llm)
    if not narrative.elapsed_ms:
        narrative.elapsed_ms = (time.perf_counter() - start) * 1000
    now = now or datetime.now(timezone.utc)
    doc = render_html(dataset, items, narrative, now, model_label)
    slug = sanitize_identifier(dataset.name.split("·")[-1], "report")[:40]
    return Report(
        html=doc,
        filename=f"report_{slug}_{now:%Y%m%d_%H%M}.html",
        fingerprint=fingerprint(items, dataset),
        title=narrative.title,
        question_count=len(items),
        answered_count=len(answered),
        narrative=narrative,
    )
