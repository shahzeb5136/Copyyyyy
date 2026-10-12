"""Build the demo brief PDF from the screenshots in ./assets.

    python docs/demo_brief/capture_screenshots.py   # app must be running
    python docs/demo_brief/build_brief.py

Output: docs/demo_brief/Ask_Your_Data_Demo_Brief.pdf
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

HERE = Path(__file__).parent
ASSETS = HERE / "assets"
OUT_PDF = HERE / "Ask_Your_Data_Demo_Brief.pdf"
QUESTION = "Which expense categories are most over budget in 2026 YTD?"
# Pixel rows of the replay-mode banner in assets/answer.png; set to None for a screenshot taken with the AI live.
ANSWER_CUT: tuple[int, int] | None = (125, 270)


def img(name: str, crop: tuple[int, int, int, int] | None = None,
        cut: tuple[int, int] | None = None) -> str | None:
    """PNG as a data URI. `crop` = (left, top, right, bottom) in screenshot CSS pixels (1440px-wide viewport);
    `cut` = (top, bottom) band of image pixels to remove."""
    path = ASSETS / name
    if not path.exists():
        return None
    data = path.read_bytes()
    if crop:
        im = Image.open(io.BytesIO(data))
        scale = im.width / 1440  # screenshots are taken at a 1440px-wide viewport
        buf = io.BytesIO()
        im.crop(tuple(round(c * scale) for c in crop)).save(buf, "PNG")
        data = buf.getvalue()
    if cut:
        im = Image.open(io.BytesIO(data))
        out = Image.new(im.mode, (im.width, im.height - (cut[1] - cut[0])), "white")
        out.paste(im.crop((0, 0, im.width, cut[0])), (0, 0))
        out.paste(im.crop((0, cut[1], im.width, im.height)), (0, cut[0]))
        buf = io.BytesIO()
        out.save(buf, "PNG")
        data = buf.getvalue()
    return "data:image/png;base64," + base64.b64encode(data).decode()


# --------------------------------------------------------------------------- landing-page annotations
def callout(n: int, text: str, lx: float, ly: float, tx: float, ty: float) -> str:
    """Numbered label at (lx, ly) with an arrow to the target point (tx, ty). Screenshot CSS-pixel space."""
    h, w = 44, 64 + 11.2 * len(text)  # pill width from text length (600-weight Inter at 20px)
    # start the arrow from the label edge closest to the target
    sx = min(max(tx, lx), lx + w)
    sy = ly + h if ty > ly + h else (ly if ty < ly else ly + h / 2)
    if ly <= ty <= ly + h:
        sx = lx if tx < lx else lx + w
    return f"""
    <line x1="{sx}" y1="{sy}" x2="{tx}" y2="{ty}" class="arr" marker-end="url(#head)"/>
    <rect x="{lx}" y="{ly}" width="{w}" height="{h}" rx="22" class="lbl"/>
    <circle cx="{lx + 22}" cy="{ly + 22}" r="15" class="num"/>
    <text x="{lx + 22}" y="{ly + 29}" class="numt">{n}</text>
    <text x="{lx + 46}" y="{ly + 29}" class="lblt">{text}</text>"""


def landing_overlay() -> str:
    b = json.loads((ASSETS / "landing_boxes.json").read_text())

    def mid(k: str, fx: float = 0.5, fy: float = 0.5) -> tuple[float, float]:
        r = b[k]
        return r["x"] + r["width"] * fx, r["y"] + r["height"] * fy

    parts = [
        callout(1, "Browse the data catalog", 640, 92, *mid("nav_catalog", 0.6, 1.3)),
        callout(2, "Pick a dataset", 30, 590, *mid("dataset_select", 0.5, 1.08)),
        callout(3, "…or click an example question", 960, 655, *mid("examples", 0.78, 1.02)),
        callout(4, "Type any question here", 420, 690, *mid("chat_input", 0.2, -0.05)),
        callout(5, "Upload your own CSV files", 860, 18, *mid("nav_upload", 1.03, 0.5)),
    ]
    return f"""<svg class="overlay" viewBox="0 0 1440 900" preserveAspectRatio="none">
      <defs><marker id="head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5"
        orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#d6336c"/></marker></defs>
      <rect x="{b['chat_input']['x'] - 6}" y="{b['chat_input']['y'] - 6}" width="{b['chat_input']['width'] + 12}"
        height="{b['chat_input']['height'] + 12}" rx="14" class="ring"/>
      {''.join(parts)}</svg>"""


# --------------------------------------------------------------------------- architecture diagram (proposed)
AZURE_SVG = """
<svg viewBox="0 0 700 330" class="diagram" role="img" aria-label="Proposed quick-path architecture on Azure">
  <defs><marker id="a2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
    orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#5b6472"/></marker></defs>
  <!-- users -->
  <text x="253" y="24" class="d-user">Finance team</text>
  <text x="420" y="24" class="d-user">HR team</text>
  <line x1="253" y1="32" x2="253" y2="100" class="d-arr" marker-end="url(#a2)"/>
  <line x1="420" y1="32" x2="420" y2="100" class="d-arr" marker-end="url(#a2)"/>
  <!-- azure boundary -->
  <rect x="150" y="48" width="545" height="274" rx="12" class="d-zone"/>
  <text x="682" y="312" class="d-zonet" text-anchor="end">Our Azure tenant</text>
  <!-- apps -->
  <rect x="165" y="70" width="345" height="112" rx="10" class="d-group"/>
  <text x="178" y="87" class="d-cap">ONE SMALL APP PER DEPARTMENT</text>
  <rect x="178" y="102" width="150" height="66" rx="8" class="d-box d-ai"/>
  <text x="253" y="130" class="d-t">Finance app</text>
  <text x="253" y="148" class="d-s">Streamlit · Microsoft sign-in</text>
  <rect x="345" y="102" width="150" height="66" rx="8" class="d-box d-ai"/>
  <text x="420" y="130" class="d-t">HR app</text>
  <text x="420" y="148" class="d-s">Streamlit · Microsoft sign-in</text>
  <!-- llm -->
  <rect x="548" y="96" width="132" height="72" rx="8" class="d-box"/>
  <text x="614" y="126" class="d-t">AI model</text>
  <text x="614" y="144" class="d-s">our own endpoint</text>
  <line x1="510" y1="132" x2="546" y2="132" class="d-arr" marker-end="url(#a2)"/>
  <!-- postgres -->
  <rect x="165" y="222" width="345" height="72" rx="10" class="d-box d-db"/>
  <text x="337" y="250" class="d-t">Azure Database for PostgreSQL</text>
  <text x="337" y="270" class="d-s">finance schema · hr schema · read-only login per app</text>
  <line x1="337" y1="184" x2="337" y2="220" class="d-arr" marker-end="url(#a2)"/>
  <text x="346" y="206" class="d-s" style="text-anchor:start">read-only SQL</text>
  <!-- sap -->
  <rect x="8" y="224" width="122" height="68" rx="8" class="d-box d-src"/>
  <text x="69" y="252" class="d-t">SAP</text>
  <text x="69" y="270" class="d-s">system of record</text>
  <line x1="130" y1="258" x2="163" y2="258" class="d-arr" marker-end="url(#a2)"/>
  <text x="69" y="312" class="d-s">scheduled copy (e.g. nightly)</text>
</svg>"""


def page_shell(body: str, n: int, total: int) -> str:
    return f"""<section class="page">{body}
      <footer><span>Ask Your Data · demo brief</span><span>{n} / {total}</span></footer></section>"""


def build_html() -> str:
    landing, catalog, answer = img("landing.png"), img("catalog.png", crop=(300, 0, 1380, 880)), img("answer.png", cut=ANSWER_CUT)
    total = 5

    p1 = f"""
    <header class="title">
      <div class="eyebrow">Demo brief · October 2026 · 5-minute read</div>
      <h1>Ask Your Data</h1>
      <p class="sub">Plain-English questions over our structured data, answered with numbers, a chart and the SQL behind it.</p>
    </header>
    <div class="summary">
      <div class="eyebrow">Executive summary</div>
      <p>Ask Your Data lets anyone type a business question in plain English (“Which cost categories are over budget
      this year?”) and, within seconds, get a written answer, a chart and the exact database query used to produce it.
      It is a working demo on made-up data, built to answer one question: is this worth piloting on our real SAP data,
      one department at a time?</p>
    </div>
    <h2>1 · Where you land</h2>
    <p class="lead">The home page. Everything a user needs is on one screen: choose a dataset, read what's in it,
    then ask.</p>
    <figure class="shot annotated"><img src="{landing}" alt="Landing page">{landing_overlay()}</figure>
    <ol class="legend">
      <li><b>Data catalog</b>: every table and column, explained in plain English.</li>
      <li><b>Dataset</b>: Finance, HR and Projects demos (one per department in a real rollout).</li>
      <li><b>Example questions</b>: one click to see it work.</li>
      <li><b>Ask box</b>: any question; follow-ups like “now split that by quarter” work.</li>
      <li><b>Bring your own data</b>: upload a CSV and query it straight away.</li>
    </ol>"""

    p2 = f"""
    <h2>2 · The data catalog (Finance example)</h2>
    <p class="lead">This is what the AI reads before it writes any query: the tables, their columns and a plain-English
    definition of each. The Finance demo has three tables: <b>departments</b>, <b>budget_actuals</b> (monthly budget
    vs. actual spend by department and cost category) and <b>revenue</b> (target vs. actual by region and product).</p>
    <figure class="shot"><img src="{catalog}" alt="Data catalog"></figure>
    <div class="callout">
      <b>Why this matters.</b> The quality of these definitions, plus a handful of business rules such as
      “YTD means closed months only”, is the single biggest lever on answer quality. In a real rollout each department
      would own and review its own catalog. That is analyst time, not a software
      project.
    </div>"""

    if answer:
        ans_block = f'<figure class="shot answer"><img src="{answer}" alt="Answer with chart and SQL"></figure>'
    else:
        ans_block = """<div class="placeholder"><div>
          <b>Screenshot pending.</b> The live answer (written explanation, chart and SQL) goes here. It will be
          captured once the app can reach the AI service from this environment.</div></div>"""
    p3 = f"""
    <h2>3 · Asking a question</h2>
    <div class="question"><span>Question asked</span>“{QUESTION}”</div>
    <p class="lead">One click. The AI writes the SQL, our database runs it, then the AI explains the result in plain
    English and picks a chart. The <b>SQL query</b> tab under the chart shows exactly what ran, so finance can check
    the logic. The result rows can also be downloaded as a CSV.</p>
    {ans_block}
    <p class="note"><b>Note on this screenshot:</b> it was captured in the app's replay mode. The SQL is the stored
    query for this sample question, but it ran live against the database, and the chart and figures are drawn from the
    real result. In normal use the AI writes the SQL on the fly and adds a short plain-English summary above the
    chart.</p>"""

    p4 = f"""
    <h2>4 · Architecture: how the demo works</h2>
    <div class="flow">
      <div class="step"><i>1</i><b>Question</b><span>Typed in plain English</span></div>
      <div class="arrow">→</div>
      <div class="step ai"><i>2</i><b>SQL agent</b><span>AI reads the data catalog, writes one SQL query and
        fixes it itself if the database rejects it</span></div>
      <div class="arrow">→</div>
      <div class="step"><i>3</i><b>Database</b><span>Query runs read-only in a locked-down SQL engine</span></div>
      <div class="arrow">→</div>
      <div class="step ai"><i>4</i><b>Analyst + chart agents</b><span>One AI explains the numbers; another picks
        the chart, which our code draws</span></div>
    </div>
    <div class="cols">
      <div>
        <h3>Building blocks</h3>
        <ul>
          <li><b>Front end:</b> Streamlit, a Python web-app framework. One codebase, no separate UI team needed.</li>
          <li><b>AI:</b> works with any large language model, so it can run on our own.</li>
          <li><b>Database:</b> today an in-memory engine (DuckDB) over CSV files of <b>synthetic</b> data.
            All database access goes through one module, which is the piece that changes to point at PostgreSQL.</li>
          <li><b>Data catalog:</b> one simple file per dataset with table/column descriptions, business rules and
            example questions.</li>
          <li><b>Packaging:</b> Docker image, about 100 automated tests, optional Microsoft/Google sign-in.</li>
        </ul>
      </div>
      <div>
        <h3>Built-in safety</h3>
        <ul>
          <li><b>Read-only.</b> Only a single <code>SELECT</code> is allowed. Updates, deletes and schema changes
            are rejected by the database's own parser.</li>
          <li><b>No AI-written code runs.</b> The chart agent returns a description of the chart; our own code draws
            it.</li>
          <li><b>Limits:</b> query timeout, row cap and a per-user question limit.</li>
          <li><b>Transparent:</b> every answer shows its SQL, raw data and a step-by-step trace.</li>
        </ul>
      </div>
    </div>
    <h2 class="mt">Proposed setup on Azure (quick path)</h2>
    {AZURE_SVG}
    <p class="note">SAP data is copied on a schedule into our Azure PostgreSQL. Each department gets its own small
    app that can only read its own schema. Users sign in with their Microsoft account.</p>"""

    p5 = """
    <h2>5 · Two ways forward</h2>
    <table class="options">
      <thead><tr><th></th><th class="qa">A · Quick path <em>(suggested start)</em></th>
        <th>B · Full build</th></tr></thead>
      <tbody>
        <tr><th>What</th><td>One Streamlit app per department (or even per person) on Azure</td>
          <td>Python API backend + custom web front end, one platform for all departments</td></tr>
        <tr><th>Data</th><td>SAP tables copied into our Azure PostgreSQL; each app reads its own schema</td>
          <td>Same PostgreSQL, behind a central API with a shared data catalog</td></tr>
        <tr><th>Access</th><td>Microsoft sign-in per app, limited to a team's Entra ID group. Everyone with
          access to an app sees all of that department's data</td>
          <td>Full Active Directory integration: roles, row-level permissions, audit log</td></tr>
        <tr><th>Effort</th><td>Small. Mostly reuses this demo; the main work is the data copy and the catalog</td>
          <td>Large. A proper multi-month software project</td></tr>
        <tr><th>Trade-off</th><td>Several small apps to maintain; best for small teams</td>
          <td>Higher cost and time; scales to the whole company</td></tr>
      </tbody>
    </table>
    <h3>Suggested rollout: department by department</h3>
    <ol class="rollout">
      <li><b>Pilot with Finance.</b> Copy the relevant SAP tables into Azure PostgreSQL, write the Finance data
        catalog with a finance analyst, and deploy one app for a handful of users.</li>
      <li><b>Check the answers</b> against numbers finance already knows. Keep it only if it is accurate and saves time.</li>
      <li><b>Repeat for the next department</b> (e.g. HR) with its own data, catalog and app.</li>
      <li><b>Move to option B</b> only if several departments use it and need finer-grained permissions.</li>
    </ol>
    <div class="closing">
      <div class="eyebrow">Important</div>
      <p><b>This is a demo only.</b> It runs on synthetic data and has not been connected to SAP or any company
      system. It was built to test whether the idea is worth pursuing, not as a finished product.</p>
      <p><b>I'd value your opinion:</b> is this worth a small Finance pilot (option A)? Is there a department you
      would rather start with?</p>
    </div>"""

    pages = [p1, p2, p3, p4, p5]
    body = "".join(page_shell(p, i + 1, total) for i, p in enumerate(pages))
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Ask Your Data · Demo brief</title>" \
           f"<style>{CSS}</style></head><body>{body}</body></html>"


CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: Inter, 'Liberation Sans', sans-serif; color: #1f2937; font-size: 10.5pt; line-height: 1.45;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.page { width: 210mm; height: 297mm; padding: 15mm 16mm 14mm; position: relative; page-break-after: always;
        overflow: hidden; }
.page:last-child { page-break-after: auto; }
footer { position: absolute; left: 16mm; right: 16mm; bottom: 8mm; display: flex; justify-content: space-between;
         font-size: 8pt; color: #9aa3af; border-top: 1px solid #e5e7eb; padding-top: 2.5mm; }
.eyebrow { font-size: 8pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: #2a6fdb; }
.title h1 { font-family: 'Inter Display', Inter, sans-serif; font-size: 30pt; margin: 1mm 0 1mm; letter-spacing: -.01em;
            color: #111827; }
.title .sub { margin: 0 0 5mm; color: #4b5563; font-size: 11.5pt; }
.summary { background: #eef4fd; border-left: 4px solid #2a6fdb; border-radius: 6px; padding: 4mm 5mm;
           margin-bottom: 6mm; }
.summary p { margin: 1.5mm 0 0; font-size: 11.5pt; line-height: 1.5; color: #111827; }
h2 { font-family: 'Inter Display', Inter, sans-serif; font-size: 15pt; margin: 0 0 2mm; color: #111827; }
h2.mt { margin-top: 6mm; }
h3 { font-size: 10.5pt; margin: 4mm 0 1.5mm; color: #111827; }
.lead { margin: 0 0 3.5mm; color: #374151; }
figure { margin: 0; }
.shot { position: relative; border: 1px solid #d6d9de; border-radius: 6px; overflow: hidden;
        box-shadow: 0 1px 4px rgba(0,0,0,.06); }
.shot img { display: block; width: 100%; }
.shot .overlay { position: absolute; inset: 0; width: 100%; height: 100%; }
.arr { stroke: #d6336c; stroke-width: 3.5; }
.lbl { fill: #d6336c; }
.num { fill: #fff; }
.numt { fill: #d6336c; font: 700 19px Inter, sans-serif; text-anchor: middle; }
.lblt { fill: #fff; font: 600 20px Inter, sans-serif; }
.ring { fill: none; stroke: #d6336c; stroke-width: 3; stroke-dasharray: 8 6; }
.legend { margin: 3.5mm 0 0; padding-left: 5mm; columns: 2; column-gap: 8mm; font-size: 9.5pt; color: #374151; }
.legend li { margin-bottom: 1mm; break-inside: avoid; }
.legend li::marker { color: #d6336c; font-weight: 700; }
.callout { margin-top: 5mm; background: #f6f8fb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 3.5mm 4.5mm;
           font-size: 10pt; color: #374151; }
.question { background: #111827; color: #fff; border-radius: 6px; padding: 3mm 4.5mm; font-size: 12pt;
            font-weight: 600; margin: 1mm 0 3.5mm; }
.question span { display: block; font-size: 7.5pt; letter-spacing: .08em; text-transform: uppercase;
                 color: #9db8ec; font-weight: 600; margin-bottom: .5mm; }
.shot.answer img { max-height: 190mm; object-fit: contain; object-position: top; }
.placeholder { border: 2px dashed #d6336c; border-radius: 8px; height: 150mm; display: flex; align-items: center;
               justify-content: center; text-align: center; padding: 0 20mm; color: #9f1239; background: #fff5f7; }
.flow { display: flex; align-items: stretch; gap: 1.5mm; margin: 2mm 0 1mm; }
.step { flex: 1; border: 1px solid #d6d9de; border-radius: 8px; padding: 2.5mm 3mm; background: #f6f8fb;
        font-size: 8.5pt; }
.step.ai { background: #eaf1fd; border-color: #2a6fdb; }
.step i { font-style: normal; display: inline-block; width: 5mm; height: 5mm; border-radius: 50%; background: #2a6fdb;
          color: #fff; text-align: center; line-height: 5mm; font-size: 7.5pt; font-weight: 700; margin-right: 1.5mm; }
.step b { font-size: 9.5pt; }
.step span { display: block; color: #4b5563; margin-top: 1mm; line-height: 1.35; }
.arrow { align-self: center; color: #9aa3af; font-size: 13pt; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 7mm; }
.cols ul { margin: 0; padding-left: 4.5mm; font-size: 9pt; color: #374151; }
.cols li { margin-bottom: 1.3mm; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 8.5pt; background: #f1f3f6; padding: 0 1mm;
       border-radius: 3px; }
.diagram { width: 100%; display: block; margin-top: 1mm; }
.d-zone { fill: #f7faff; stroke: #2a6fdb; stroke-dasharray: 6 4; stroke-width: 1.2; }
.d-zonet { font: 600 10px Inter, sans-serif; fill: #2a6fdb; letter-spacing: .06em; text-transform: uppercase; }
.d-group { fill: #fff; stroke: #c7d6f2; }
.d-cap { font: 600 8.5px Inter, sans-serif; fill: #6b7280; letter-spacing: .08em; }
.d-box { fill: #fff; stroke: #9aa3af; stroke-width: 1.2; }
.d-ai { fill: #eaf1fd; stroke: #2a6fdb; }
.d-db { fill: #e7f6ec; stroke: #0f7b38; }
.d-src { fill: #fff7e6; stroke: #b7791f; }
.d-t { font: 700 12px Inter, sans-serif; fill: #111827; text-anchor: middle; }
.d-s { font: 400 9.5px Inter, sans-serif; fill: #4b5563; text-anchor: middle; }
.d-user { font: 600 11px Inter, sans-serif; fill: #111827; text-anchor: middle; }
.d-arr { stroke: #5b6472; stroke-width: 1.4; }
.note { font-size: 9pt; color: #4b5563; margin: 2mm 0 0; }
table.options { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 9.5pt; margin-top: 2mm; }
.options th, .options td { text-align: left; vertical-align: top; padding: 2.4mm 3mm; border-bottom: 1px solid #e5e7eb; }
.options thead th { font-size: 10.5pt; color: #111827; border-bottom: 2px solid #d6d9de; }
.options thead th.qa { color: #0f7b38; }
.options thead em { font-style: normal; font-weight: 500; font-size: 8.5pt; color: #6b7280; }
.options tbody th { width: 19mm; color: #6b7280; font-weight: 600; font-size: 8.5pt; text-transform: uppercase;
                    letter-spacing: .05em; }
.options td:nth-child(2) { background: #f3fbf6; }
.rollout { padding-left: 5mm; margin: 0; font-size: 10pt; color: #374151; }
.rollout li { margin-bottom: 1.8mm; }
.closing { margin-top: 6mm; border: 2px solid #d6336c; border-radius: 8px; padding: 4mm 5mm; background: #fff5f7; }
.closing .eyebrow { color: #d6336c; }
.closing p { margin: 1.8mm 0 0; font-size: 10.5pt; color: #111827; }
"""


async def render_pdf(html: str) -> None:
    html_path = HERE / "brief.html"
    html_path.write_text(html)
    async with async_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        browser = await p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = await browser.new_page()
        await page.goto(html_path.resolve().as_uri())
        await page.wait_for_timeout(500)
        await page.pdf(path=str(OUT_PDF), format="A4", print_background=True, prefer_css_page_size=True)
        await browser.close()
    html_path.unlink()


if __name__ == "__main__":
    asyncio.run(render_pdf(build_html()))
    print(f"Wrote {OUT_PDF}")
