"""A sensible default dashboard built WITHOUT an LLM, from the semantic model alone.

Used when no LLM is configured (local development, tests) and as the safety net
when the LLM fails repeatedly. It is deliberately conservative: KPIs, a trend,
breakdowns by the main dimensions, a budget-vs-actual comparison when it spots one.
"""

from __future__ import annotations

import re

from ..engine.profile import TableProfile
from ..spec.models import ChartConfig, DashboardDraft, Layout, Metric, SemanticModel, Step, TextConfig, Widget, slugify

_PAIRS = [("budget", "actual"), ("target", "actual"), ("plan", "actual"), ("forecast", "actual"), ("planned", "actual")]


def _label(name: str) -> str:
    s = name if (" " in name or "(" in name) else name.replace("_", " ")
    return s[:1].upper() + s[1:]


def _fmt(col) -> str:
    return {"currency": "currency", "percent": "percent"}.get(col.semantic_type, "number")


def _bucket_unit(profile: TableProfile, time_col: str) -> str:
    c = profile.column(time_col)
    if not c or not c.dates:
        return "month"
    days = c.dates.span_days
    if c.dates.granularity == "year" or days > 365 * 10:
        return "year"
    if c.dates.granularity == "quarter" or days > 365 * 3:
        return "quarter"
    if days <= 60:
        return "day"
    if days <= 120:
        return "week"
    return "month"


def default_dashboard(profile: TableProfile, model: SemanticModel) -> DashboardDraft:
    measures = [c for c in model.measures() if profile.column(c.name) and profile.column(c.name).data_type in ("integer", "number")]
    # Rank measures: currency first, then counts/quantities, then the rest; prefer wide ranges.
    order = {"currency": 0, "count": 1, "quantity": 1, "ratio": 3, "percent": 3, "score": 2}
    measures.sort(key=lambda c: (order.get(c.semantic_type, 2), -(profile.column(c.name).numeric.sum or 0 if profile.column(c.name).numeric else 0)))
    dims = [c for c in model.dimensions() if profile.column(c.name) and 2 <= profile.column(c.name).distinct <= 60]
    # Prefer real categories (3-30 values) over booleans/years; then fewer values first.
    dims.sort(key=lambda c: (0 if 3 <= profile.column(c.name).distinct <= 30 and c.semantic_type not in ("boolean", "year", "period") else 1,
                             profile.column(c.name).distinct))
    time_col = model.time_column or (model.time_columns()[0].name if model.time_columns() else "")
    widgets: list[Widget] = []

    # KPIs
    for m in measures[:4]:
        fn = m.aggregation if m.aggregation in ("sum", "mean", "median", "min", "max", "count") else "sum"
        label = f"{'Total' if fn == 'sum' else 'Average' if fn == 'mean' else fn.title()} {_label(m.name).lower()}"
        widgets.append(Widget(id=slugify("kpi_" + m.name), title=label, subtitle=f"{fn} of {m.name} over all rows",
                              steps=[Step(op="aggregate", metrics=[Metric(column=m.name, fn=fn, name="value")])],
                              chart=ChartConfig(type="kpi", y=["value"], value_format=_fmt(m), y_title=label),
                              layout=Layout(w=3)))
    if not measures:
        widgets.append(Widget(id="kpi_rows", title="Rows", subtitle="number of rows in the file",
                              steps=[Step(op="aggregate", metrics=[Metric(column="", fn="count", name="value")])],
                              chart=ChartConfig(type="kpi", y=["value"]), layout=Layout(w=3)))

    main = measures[0] if measures else None
    pair = _find_pair(measures)

    # Trend
    if time_col and main:
        unit = _bucket_unit(profile, time_col)
        ys = [p.name for p in pair] if pair else [main.name]
        metrics = [Metric(column=y, fn=model.column(y).aggregation if model.column(y) and model.column(y).aggregation in ("sum", "mean") else "sum", name=slugify(y)) for y in ys]
        widgets.append(Widget(id="trend", title=f"{_label(ys[0]) if len(ys) == 1 else ' vs '.join(_label(y) for y in ys)} over time",
                              subtitle=f"by {unit}, {metrics[0].fn} of {', '.join(ys)}",
                              steps=[Step(op="date_bucket", column=time_col, unit=unit, name="period"),
                                     Step(op="aggregate", group_by=["period"], metrics=metrics),
                                     Step(op="sort", columns=["period"], descending=False)],
                              chart=ChartConfig(type="line", x="period", y=[m.name for m in metrics], value_format=_fmt(main)),
                              layout=Layout(w=12)))

    # Budget vs actual by the first dimension
    if pair and dims:
        d = dims[0]
        a, b = pair
        widgets.append(Widget(id=f"{slugify(a.name)}_vs_{slugify(b.name)}_by_{slugify(d.name)}",
                              title=f"{_label(a.name)} vs {_label(b.name).lower()} by {_label(d.name).lower()}",
                              subtitle=f"sum of {a.name} and {b.name} per {d.name}",
                              steps=[Step(op="aggregate", group_by=[d.name], metrics=[Metric(column=a.name, fn="sum", name=slugify(a.name)), Metric(column=b.name, fn="sum", name=slugify(b.name))]),
                                     Step(op="derive", name="variance", expression=f"`{b.name}` - `{a.name}`".replace(f"`{b.name}`", slugify(b.name)).replace(f"`{a.name}`", slugify(a.name))),
                                     Step(op="sort", columns=[d.name], descending=False) if d.semantic_type in ("period", "year")
                                     else Step(op="sort", columns=[slugify(b.name)], descending=True)],
                              chart=ChartConfig(type="bar", x=d.name, y=[slugify(a.name), slugify(b.name)], value_format=_fmt(a),
                                                orientation="h" if profile.column(d.name).distinct > 8 else "v"),
                              layout=Layout(w=6)))

    # Breakdowns by dimension
    used = 0
    for d in dims:
        if used >= 3 or not main:
            break
        if pair and d is dims[0]:
            continue
        fn = main.aggregation if main.aggregation in ("sum", "mean") else "sum"
        n = profile.column(d.name).distinct
        ordered = d.semantic_type in ("period", "year")
        widgets.append(Widget(id=f"{slugify(main.name)}_by_{slugify(d.name)}",
                              title=f"{_label(main.name)} by {_label(d.name).lower()}",
                              subtitle=f"{fn} of {main.name} per {d.name}" + (", top 12" if n > 12 and not ordered else ""),
                              steps=[Step(op="aggregate", group_by=[d.name], metrics=[Metric(column=main.name, fn=fn, name="value")])]
                                    + ([Step(op="sort", columns=[d.name], descending=False)] if ordered
                                       else [Step(op="top_n", column="value", n=12, value="Other" if n > 12 else "")]),
                              chart=ChartConfig(type="bar", x=d.name, y=["value"], value_format=_fmt(main), sort="none" if ordered else "desc",
                                                orientation="h" if n > 8 else "v", y_title=_label(main.name)),
                              layout=Layout(w=6)))
        used += 1

    # Second measure by the main dimension (share), as a donut when few categories
    if len(measures) > 1 and dims and not pair:
        d, m2 = dims[0], measures[1]
        if profile.column(d.name).distinct <= 6 and m2.semantic_type in ("currency", "count", "quantity"):
            widgets.append(Widget(id=f"{slugify(m2.name)}_share_by_{slugify(d.name)}",
                                  title=f"Share of {_label(m2.name).lower()} by {_label(d.name).lower()}",
                                  subtitle=f"sum of {m2.name} per {d.name}",
                                  steps=[Step(op="aggregate", group_by=[d.name], metrics=[Metric(column=m2.name, fn="sum", name="value")])],
                                  chart=ChartConfig(type="pie", x=d.name, y=["value"], value_format=_fmt(m2)), layout=Layout(w=6)))

    # Heatmap: dimension x period for the main measure (when both exist and the grid is small)
    if time_col and main and dims and profile.column(dims[0].name).distinct <= 20:
        unit = _bucket_unit(profile, time_col)
        if unit in ("month", "quarter", "year"):
            widgets.append(Widget(id=f"{slugify(main.name)}_heatmap", title=f"{_label(main.name)} by {_label(dims[0].name).lower()} and {unit}",
                                  subtitle=f"sum of {main.name}",
                                  steps=[Step(op="date_bucket", column=time_col, unit=unit, name="period"),
                                         Step(op="aggregate", group_by=[dims[0].name, "period"], metrics=[Metric(column=main.name, fn="sum", name="value")])],
                                  chart=ChartConfig(type="heatmap", x="period", y=[dims[0].name], z="value", value_format=_fmt(main)),
                                  layout=Layout(w=12)))

    # Executive summary under the trend (or the first chart)
    anchor = next((w for w in widgets if w.chart.type != "kpi"), None)
    if anchor:
        idx = widgets.index(anchor)
        widgets.insert(idx + 1, Widget(id="executive_summary", type="text", title="Executive summary",
                                       text=TextConfig(mode="generated", source_widget_id=anchor.id), layout=Layout(w=12)))
    return DashboardDraft(title=model.title or "Dashboard", description=model.description, widgets=widgets)


def _find_pair(measures):
    names = {re.sub(r"[^a-z]", "", m.name.lower()): m for m in measures}
    for a, b in _PAIRS:
        ma = next((m for k, m in names.items() if a in k), None)
        mb = next((m for k, m in names.items() if b in k and m is not ma), None)
        if ma and mb:
            return (ma, mb)
    return None
