"""Turn a ``ChartConfig`` + a DataFrame into a Plotly figure (as JSON), KPI tile or table.

The LLM never writes plotting code: it picks a chart type and columns. This module
validates that choice against the actual data, repairs what it safely can, and
renders a consistently styled figure. Colours are the validated colour-blind-safe
palette used throughout the app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

from ..spec.models import ChartConfig
from .profile import frame_to_records
from .transforms import is_datetime, is_numeric

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
DIVERGING = [[0.0, "#c13b3b"], [0.25, "#e88a86"], [0.5, "#f0efec"], [0.75, "#86b6ef"], [1.0, "#1c5cab"]]
NEUTRAL = "#8b8a85"
NEGATIVE = "#e34948"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
          "info": "#2a78d6", "neutral": "#a3a29c"}
STATUS_WORDS = {
    "on track": "good", "completed": "good", "complete": "good", "closed": "good", "done": "good", "green": "good",
    "approved": "good", "active": "info", "in progress": "info", "at risk": "warning", "amber": "warning",
    "pending": "warning", "off track": "critical", "blocked": "critical", "overdue": "critical", "red": "critical",
    "cancelled": "neutral", "not started": "neutral", "on hold": "neutral", "open": "neutral",
}
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
INK = "#1f2937"
INK_MUTED = "#6b7280"
GRID = "#eceef2"
AXIS = "#d6d9de"
MAX_CATEGORIES = 40
MAX_SERIES = 8
CURRENCY_PREFIX = {"USD": "$", "EUR": "€", "GBP": "£", "AED": "AED ", "SAR": "SAR ", "INR": "₹", "JPY": "¥",
                   "QAR": "QAR ", "KWD": "KWD ", "OMR": "OMR ", "BHD": "BHD ", "EGP": "EGP "}


class ChartError(Exception):
    pass


@dataclass
class FigureResult:
    kind: str  # plotly | kpi | table
    figure: dict | None = None
    kpi: dict | None = None
    table: dict | None = None
    note: str = ""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def pretty(name: str) -> str:
    """'total_actual_usd' -> 'Total actual (USD)'; 'Net Sales (AED)' stays as is."""
    s = str(name)
    if " " in s or "(" in s:
        return s
    words = s.replace("_", " ").strip().split()
    unit = ""
    if words and words[-1].lower() in {"usd", "eur", "gbp", "aed", "sar", "inr", "qar"}:
        unit = f" ({words.pop().upper()})"
    elif words and words[-1].lower() in {"pct", "percent", "percentage"}:
        words.pop()
        unit = " (%)"
    text = " ".join(words) or s
    return text[:1].upper() + text[1:] + unit


def _maybe_datetime(s: pd.Series) -> pd.Series:
    if is_datetime(s) or is_numeric(s):
        return s
    try:
        sample = s.dropna().astype(str).head(50)
        if len(sample) and sample.str.match(r"^\d{4}-\d{2}(-\d{2})?").all():
            return pd.to_datetime(s, errors="coerce")
    except Exception:  # noqa: BLE001
        pass
    return s


def _resolver(df: pd.DataFrame):
    lookup = {str(c).lower().strip(): c for c in df.columns}

    def resolve(name: str) -> str:
        if not name:
            return ""
        return lookup.get(str(name).lower().strip().strip('"'), "")

    return resolve


def _is_fraction(values: pd.Series) -> bool:
    v = pd.to_numeric(values, errors="coerce").dropna()
    return len(v) > 0 and v.abs().max() <= 1.5


def _axis_format(values: pd.Series, value_format: str, currency: str) -> dict:
    v = pd.to_numeric(values, errors="coerce").dropna()
    big = len(v) and v.abs().max() >= 10_000
    if value_format == "percent":
        if _is_fraction(v):
            return {"tickformat": ".0%", "hoverformat": ".1%"}
        return {"ticksuffix": "%", "tickformat": ",.0f" if big else ",.3~f", "hoverformat": ",.1f"}
    prefix = CURRENCY_PREFIX.get(currency.upper(), f"{currency} ") if value_format == "currency" and currency else ""
    return {"tickprefix": prefix, "tickformat": "~s" if big else ",.4~f", "hoverformat": ",.0f" if big else ",.2f"}


def format_value(value, value_format: str = "number", currency: str = "", column: str = "") -> str:
    if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d %b %Y")
    if isinstance(value, str):
        return value
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    col = column.lower()
    if value_format == "percent" or col.endswith(("_pct", "_percent", "_rate", "%")):
        return f"{value:.1%}" if abs(value) <= 1.5 and not col.endswith("_pct") else f"{value:,.1f}%"
    prefix = ""
    if value_format == "currency":
        prefix = CURRENCY_PREFIX.get((currency or "").upper(), f"{currency} " if currency else "")
    a = abs(value)
    sign = "-" if value < 0 else ""
    if a >= 1e9:
        return f"{sign}{prefix}{a / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{sign}{prefix}{a / 1e6:,.2f}M"
    if a >= 1e4:
        return f"{sign}{prefix}{a / 1e3:,.1f}K"
    if float(value).is_integer():
        return f"{sign}{prefix}{a:,.0f}"
    return f"{sign}{prefix}{a:,.2f}"


def _tint(hex_color: str, amount: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (round(c + (255 - c) * amount) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def color_map_for(values) -> dict[str, str]:
    uniq = list(pd.unique(pd.Series(list(values)).dropna().astype(str)))
    lowered = [u.lower() for u in uniq]
    if uniq and all(u in STATUS_WORDS for u in lowered):
        roles = [STATUS_WORDS[u] for u in lowered]
        if len(set(roles)) == len(roles):
            return {u: STATUS[r] for u, r in zip(uniq, roles)}
    return {u: PALETTE[i % len(PALETTE)] for i, u in enumerate(uniq)}


def _fold_series(df: pd.DataFrame, by: str, value_cols: list[str], keys: list[str], keep: int) -> tuple[pd.DataFrame, str]:
    if df[by].nunique() <= keep + 1:
        return df, ""
    totals = df.groupby(by, dropna=False)[value_cols[0]].sum().abs().sort_values(ascending=False)
    top = set(totals.index[:keep])
    out = df.copy()
    out[by] = out[by].where(out[by].isin(top), "Other")
    out = out.groupby(keys + [by], dropna=False, sort=False)[value_cols].sum().reset_index()
    return out, f"{len(totals) - keep} smaller groups folded into 'Other'."


def _style(fig: go.Figure, height: int = 380, legend: bool = True) -> go.Figure:
    fig.update_layout(
        template="plotly_white", font=dict(family=FONT, size=13, color=INK),
        margin=dict(l=8, r=16, t=36 if legend else 12, b=8), height=height, autosize=True,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", colorway=PALETTE, showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, title_text="",
                    font=dict(size=12, color=INK_MUTED), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="white", bordercolor=AXIS, font=dict(family=FONT, size=12, color=INK)),
        bargap=0.28, bargroupgap=0.06, barcornerradius=4, title=None,
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, ticks="", tickfont=dict(color=INK_MUTED),
                     title_font=dict(size=12, color=INK_MUTED), zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, linecolor=AXIS, showline=False, ticks="",
                     tickfont=dict(color=INK_MUTED), title_font=dict(size=12, color=INK_MUTED),
                     zeroline=True, zerolinecolor=AXIS, zerolinewidth=1, automargin=True)
    fig.update_traces(selector=dict(type="bar"), marker_line_width=0)
    return fig


def figure_to_json(fig: go.Figure) -> dict:
    return json.loads(pio.to_json(fig, validate=False))


def _value_labels(fig: go.Figure, fmt: str, currency: str) -> None:
    if fmt == "percent":
        fig.update_traces(texttemplate="%{value:.1f}%" if False else "%{y:.1f}", textposition="outside")
    else:
        fig.update_traces(texttemplate="%{y:,.3s}", textposition="outside")
    fig.update_traces(textfont=dict(size=11, color=INK_MUTED), cliponaxis=False)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def build_figure(chart: ChartConfig, df: pd.DataFrame, currency: str = "", max_table_rows: int = 500) -> FigureResult:
    if df is None:
        raise ChartError("No data.")
    t = chart.type
    if t == "table":
        return FigureResult("table", table=frame_to_records(df, max_table_rows))
    if df.empty:
        raise ChartError("The query returned no rows.")
    df = df.copy()
    r = _resolver(df)
    x, color, size, z = r(chart.x), r(chart.color), r(chart.size), r(chart.z)
    ys = [c for c in (r(y) for y in chart.y) if c]
    numeric_ys = [c for c in ys if is_numeric(df[c])]
    fmt = chart.value_format
    note = ""

    if t == "kpi":
        return _kpi(chart, df, numeric_ys, currency)
    if t == "gantt":
        return _gantt(chart, df, r)

    if t in ("bar", "line", "area", "pie", "funnel", "waterfall"):
        if not x:
            # Fall back to the first non-numeric column.
            x = next((c for c in df.columns if not is_numeric(df[c])), "")
            if not x:
                raise ChartError(f"'{chart.x}' is not a column in the result ({', '.join(map(str, df.columns))}).")
        if not numeric_ys:
            numeric_ys = [c for c in df.columns if c != x and c != color and is_numeric(df[c])][:1]
            if not numeric_ys:
                raise ChartError("No numeric column to plot.")
        if t in ("line", "area"):
            df[x] = _maybe_datetime(df[x])
        if color == x:
            color = ""

    labels = {c: pretty(c) for c in df.columns}
    if chart.x_title and x:
        labels[x] = chart.x_title

    if t == "bar":
        return _bar(chart, df, x, color, numeric_ys, labels, fmt, currency)
    if t in ("line", "area"):
        return _line(chart, df, x, color, numeric_ys, labels, fmt, currency, t)
    if t == "pie":
        return _pie(df, x, numeric_ys[0], labels, note)
    if t == "funnel":
        value_col = numeric_ys[0]
        fig = px.funnel(df, x=value_col, y=x, labels=labels)
        fig.update_traces(marker_color=PALETTE[0], textinfo="value+percent initial", connector=dict(fillcolor="#eef3fb"))
        return FigureResult("plotly", figure_to_json(_style(fig, max(300, 56 * len(df) + 80), legend=False)))
    if t == "waterfall":
        return _waterfall(df, x, numeric_ys[0], fmt, currency)
    if t == "scatter":
        return _scatter(chart, df, x, color, size, numeric_ys, labels, fmt, currency)
    if t == "histogram":
        col = x if x and is_numeric(df[x]) else (numeric_ys[0] if numeric_ys else "")
        if not col:
            raise ChartError("A histogram needs a numeric column.")
        if color and df[color].nunique() > 4:
            color = ""
        fig = px.histogram(df, x=col, color=color or None, labels=labels, nbins=30,
                           color_discrete_map=color_map_for(df[color]) if color else None,
                           barmode="overlay" if color else "relative", opacity=0.85 if color else 1)
        if not color:
            fig.update_traces(marker_color=PALETTE[0])
        fig.update_xaxes(**_axis_format(df[col], fmt, currency))
        fig.update_yaxes(title_text=chart.y_title or "Count")
        fig.update_layout(bargap=0.06)
        return FigureResult("plotly", figure_to_json(_style(fig, 360, legend=bool(color))))
    if t == "box":
        ycol = numeric_ys[0] if numeric_ys else ""
        if not ycol:
            raise ChartError("A box plot needs a numeric column.")
        fig = px.box(df, x=x or None, y=ycol, labels=labels, points="outliers")
        fig.update_traces(marker_color=PALETTE[0], line_color=PALETTE[0], fillcolor="rgba(42,120,214,0.15)")
        fig.update_yaxes(**_axis_format(df[ycol], fmt, currency))
        return FigureResult("plotly", figure_to_json(_style(fig, 380, legend=False)))
    if t == "heatmap":
        return _heatmap(df, x, ys, z, fmt, currency)
    if t == "treemap":
        return _treemap(chart, df, r, x, numeric_ys)
    raise ChartError(f"Unsupported chart type '{t}'.")


def _kpi(chart: ChartConfig, df: pd.DataFrame, numeric_ys: list[str], currency: str) -> FigureResult:
    cols = numeric_ys or [c for c in df.columns if is_numeric(df[c])]
    if not cols:
        raise ChartError("A KPI needs a numeric value column.")
    col = cols[0]
    if len(df) > 1:
        # Several rows: a KPI shows the first row (the steps should aggregate to one row).
        note = f"Showing the first of {len(df)} rows."
    else:
        note = ""
    raw = df.iloc[0][col]
    value = None if pd.isna(raw) else float(raw)
    return FigureResult("kpi", kpi={"column": col, "label": chart.y_title or pretty(col), "value": value,
                                    "value_formatted": format_value(raw, chart.value_format, currency, col),
                                    "value_format": chart.value_format}, note=note)


def _categorical(s: pd.Series) -> pd.Series:
    """Numeric category columns (years, codes) must not become a continuous axis."""
    if is_numeric(s):
        vals = pd.to_numeric(s, errors="coerce")
        if (vals.dropna() % 1 == 0).all():
            return vals.astype("Int64").astype("string")
        return vals.astype("string")
    return s


def _bar(chart, df, x, color, numeric_ys, labels, fmt, currency) -> FigureResult:
    note = ""
    df = df.copy()
    df[x] = _categorical(df[x])
    if color:
        df[color] = _categorical(df[color])
    plot_df, measure_col, value_col = df, None, numeric_ys[0]
    if len(numeric_ys) > 1:
        measure_col, value_col = "Measure", "__value"
        plot_df = df.melt(id_vars=[x], value_vars=numeric_ys, var_name="Measure", value_name="__value")
        plot_df["Measure"] = plot_df["Measure"].map(pretty)
        labels["__value"] = chart.y_title or "Value"
        color = measure_col
    elif color:
        if df[color].nunique() > MAX_SERIES:
            plot_df, note = _fold_series(df, color, [value_col], [x], MAX_SERIES - 1)
    cats = plot_df.groupby(x, sort=False, dropna=False)[value_col].sum()
    if len(cats) > MAX_CATEGORIES:
        keep = cats.abs().sort_values(ascending=False).index[:MAX_CATEGORIES]
        plot_df = plot_df[plot_df[x].isin(keep)]
        note = f"Showing the {MAX_CATEGORIES} largest of {len(cats)} categories."
        cats = cats[cats.index.isin(keep)]
    order = list(cats.index)
    if chart.sort in ("asc", "desc"):
        order = list(cats.sort_values(ascending=chart.sort == "asc").index)
    x_is_time = is_datetime(plot_df[x])
    horizontal = chart.orientation == "h" or (len(order) > 12 and not x_is_time)
    if horizontal and chart.sort == "none":
        order = order[::-1]  # plotly draws horizontal categories bottom-up; keep the first row on top
    cmap = color_map_for(plot_df[color]) if color else None
    orders = {x: order} if not x_is_time else {}
    if horizontal and color and chart.barmode == "group":
        orders[color] = list(pd.unique(plot_df[color].dropna()))[::-1]
    common = dict(color=color or None, color_discrete_map=cmap, labels=labels, category_orders=orders or None,
                  barmode=chart.barmode)
    if horizontal:
        fig = px.bar(plot_df, y=x, x=value_col, orientation="h", **common)
        fig.update_xaxes(**_axis_format(plot_df[value_col], fmt, currency), showgrid=True, gridcolor=GRID,
                         title_text=chart.y_title or (labels.get(value_col, "") if not measure_col else ""))
        fig.update_yaxes(showgrid=False, title_text="" if len(order) > 3 else labels.get(x, pretty(x)))
        height = max(300, min(900, 30 * len(order) * (1.5 if color and chart.barmode == "group" else 1) + 90))
        if chart.show_values:
            fig.update_traces(texttemplate="%{x:,.3s}", textposition="outside", textfont=dict(size=11, color=INK_MUTED),
                              cliponaxis=False)
    else:
        fig = px.bar(plot_df, x=x, y=value_col, **common)
        fig.update_yaxes(**_axis_format(plot_df[value_col], fmt, currency),
                         title_text=chart.y_title or (labels.get(value_col, "") if not measure_col else ""))
        height = 380
        if chart.show_values:
            fig.update_traces(texttemplate="%{y:,.3s}", textposition="outside", textfont=dict(size=11, color=INK_MUTED),
                              cliponaxis=False)
    if not color:
        fig.update_traces(marker_color=PALETTE[0])
    if x_is_time:
        fig.update_xaxes(tickformat="%b %Y")
    fig = _style(fig, height, legend=bool(color))
    n_cats = len(order)
    fig.update_layout(bargap=0.55 if n_cats <= 3 else 0.42 if n_cats <= 6 else 0.3)
    if horizontal and color and chart.barmode == "group":
        fig.update_layout(legend_traceorder="reversed")
    return FigureResult("plotly", figure_to_json(fig), note=note)


def _line(chart, df, x, color, numeric_ys, labels, fmt, currency, t) -> FigureResult:
    note = ""
    plot_df = df.sort_values(x, kind="mergesort")
    value_col = numeric_ys[0]
    if len(numeric_ys) > 1:
        plot_df = plot_df.melt(id_vars=[x] + ([color] if color else []), value_vars=numeric_ys[:6],
                               var_name="Measure", value_name="__value")
        plot_df["Measure"] = plot_df["Measure"].map(pretty)
        color, value_col = "Measure", "__value"
        labels["__value"] = chart.y_title or "Value"
    elif color and plot_df[color].nunique() > MAX_SERIES:
        plot_df, note = _fold_series(plot_df, color, [value_col], [x], MAX_SERIES - 1)
        plot_df = plot_df.sort_values(x, kind="mergesort")
    cmap = color_map_for(plot_df[color]) if color else None
    fn = px.line if t == "line" else px.area
    fig = fn(plot_df, x=x, y=value_col, color=color or None, color_discrete_map=cmap, labels=labels)
    if t == "line":
        n_points = plot_df[x].nunique()
        fig.update_traces(line=dict(width=2.2), mode="lines+markers" if n_points <= 36 else "lines",
                          marker=dict(size=7, line=dict(width=1.5, color="white")))
    else:
        fig.update_traces(line=dict(width=1.5))
    if not color:
        fig.update_traces(line_color=PALETTE[0])
    fig.update_yaxes(**_axis_format(plot_df[value_col], fmt, currency),
                     title_text=chart.y_title or ("" if value_col == "__value" else labels.get(value_col, "")))
    fig.update_layout(hovermode="x unified")
    if is_datetime(plot_df[x]):
        fig.update_xaxes(tickformat="%b %Y")
    if chart.show_values and t == "line" and plot_df[x].nunique() <= 24:
        fig.update_traces(mode="lines+markers+text", texttemplate="%{y:,.3s}", textposition="top center",
                          textfont=dict(size=10, color=INK_MUTED))
    return FigureResult("plotly", figure_to_json(_style(fig, 380, legend=bool(color))), note=note)


def _pie(df, x, value_col, labels, note) -> FigureResult:
    plot_df = df[[x, value_col]].copy()
    plot_df = plot_df[plot_df[value_col] > 0]
    if plot_df.empty:
        raise ChartError("A donut needs positive values.")
    plot_df = plot_df.groupby(x, sort=False, as_index=False)[value_col].sum().sort_values(value_col, ascending=False)
    if len(plot_df) > 6:
        top, rest = plot_df.head(5), plot_df.iloc[5:]
        plot_df = pd.concat([top, pd.DataFrame({x: ["Other"], value_col: [rest[value_col].sum()]})])
        note = f"{len(rest)} smaller slices grouped into 'Other'."
    cmap = color_map_for(plot_df[x])
    fig = px.pie(plot_df, names=x, values=value_col, hole=0.58, color=x, color_discrete_map=cmap, labels=labels)
    fig.update_traces(sort=False, direction="clockwise", textinfo="percent", textposition="outside",
                      marker=dict(line=dict(color="white", width=2)),
                      hovertemplate="%{label}<br>%{value:,.0f} (%{percent})<extra></extra>")
    fig = _style(fig, 360, legend=True)
    fig.update_layout(legend=dict(orientation="v", y=0.5, yanchor="middle", x=1.02, xanchor="left"),
                      margin=dict(l=8, r=8, t=24, b=24))
    return FigureResult("plotly", figure_to_json(fig), note=note)


def _waterfall(df, x, value_col, fmt, currency) -> FigureResult:
    steps = df[[x, value_col]].dropna()
    if len(steps) > MAX_CATEGORIES:
        raise ChartError("Too many steps for a waterfall.")
    xs = steps[x].astype(str).tolist() + ["Total"]
    ys_ = steps[value_col].tolist() + [None]
    fig = go.Figure(go.Waterfall(
        x=xs, y=ys_, measure=["relative"] * len(steps) + ["total"],
        increasing=dict(marker=dict(color=PALETTE[0])), decreasing=dict(marker=dict(color=NEGATIVE)),
        totals=dict(marker=dict(color=NEUTRAL)), connector=dict(line=dict(color=AXIS, width=1)),
    ))
    fig.update_yaxes(**_axis_format(steps[value_col], fmt, currency))
    return FigureResult("plotly", figure_to_json(_style(fig, 380, legend=False)))


def _scatter(chart, df, x, color, size, numeric_ys, labels, fmt, currency) -> FigureResult:
    note = ""
    xcol = x if x and is_numeric(df[x]) else ""
    ycol = numeric_ys[0] if numeric_ys else ""
    if not xcol or not ycol:
        raise ChartError("A scatter plot needs two numeric columns (x and y).")
    if color and df[color].nunique() > 8:
        color = ""
        note = "Too many groups to colour individually."
    if len(df) > 5_000:
        df = df.sample(5_000, random_state=0)
        note = "Showing a random sample of 5,000 points."
    text_cols = [c for c in df.columns if not is_numeric(df[c]) and c != color]
    cmap = color_map_for(df[color]) if color else None
    fig = px.scatter(df, x=xcol, y=ycol, color=color or None, color_discrete_map=cmap,
                     size=size if size and is_numeric(df[size]) else None,
                     hover_name=text_cols[0] if text_cols else None, labels=labels)
    fig.update_traces(marker=dict(line=dict(width=1.5, color="white"), opacity=0.85, **({} if size else {"size": 9})))
    if not color:
        fig.update_traces(marker_color=PALETTE[0])
    fig.update_xaxes(**_axis_format(df[xcol], "number", currency), showgrid=True, gridcolor=GRID)
    fig.update_yaxes(**_axis_format(df[ycol], fmt, currency), title_text=chart.y_title or labels.get(ycol, ""))
    return FigureResult("plotly", figure_to_json(_style(fig, 400, legend=bool(color))), note=note)


def _heatmap(df, x, ys, z, fmt, currency) -> FigureResult:
    row_dim = ys[0] if ys else ""
    if row_dim and is_numeric(df[row_dim]) and not z:
        z, row_dim = row_dim, ""
    if not row_dim:
        row_dim = next((c for c in df.columns if c not in (x, z) and not is_numeric(df[c])), "")
    if not z:
        z = next((c for c in df.columns if c not in (x, row_dim) and is_numeric(df[c])), "")
    if not (x and row_dim and z and is_numeric(df[z])):
        raise ChartError("A heatmap needs x, y and a numeric z column.")
    grid = df.pivot_table(index=row_dim, columns=x, values=z, aggfunc="sum")
    if grid.shape[0] > 40 or grid.shape[1] > 40:
        raise ChartError("Too many rows/columns for a heatmap (max 40 x 40).")
    if is_datetime(df[x]):
        grid.columns = [c.strftime("%Y-%m-%d") if isinstance(c, pd.Timestamp) else c for c in grid.columns]
    grid.columns = [str(c) for c in grid.columns]
    grid.index = [str(i) for i in grid.index]
    diverging = (grid.min().min() < 0) and (grid.max().max() > 0)
    fig = px.imshow(grid, aspect="auto", labels=dict(x=pretty(x), y=pretty(row_dim), color=pretty(z)),
                    color_continuous_scale=DIVERGING if diverging else SEQUENTIAL,
                    color_continuous_midpoint=0 if diverging else None, text_auto=False)
    if grid.shape[0] * grid.shape[1] <= 120:
        text = grid.map(lambda v: "" if pd.isna(v) else format_value(v, fmt, currency, z))
        fig.update_traces(text=text.values, texttemplate="%{text}", textfont=dict(size=11))
    fig.update_xaxes(side="bottom", showgrid=False)
    fig.update_yaxes(showgrid=False, zeroline=False)
    fig.update_layout(coloraxis_colorbar=dict(thickness=12, title=dict(text="")))
    return FigureResult("plotly", figure_to_json(_style(fig, max(300, 28 * grid.shape[0] + 120), legend=False)))


def _treemap(chart, df, r, x, numeric_ys) -> FigureResult:
    path = [c for c in (r(p) for p in chart.path) if c] or ([x] if x else [])
    value_col = numeric_ys[0] if numeric_ys else next((c for c in df.columns if is_numeric(df[c])), "")
    if not path or not value_col:
        raise ChartError("A treemap needs category path columns and a value column.")
    plot_df = df[df[value_col] > 0].copy()
    if plot_df.empty:
        raise ChartError("A treemap needs positive values.")
    for p in path:
        plot_df[p] = plot_df[p].astype("string").fillna("(blank)").astype(str)
    cmap = {k: _tint(v, 0.55) for k, v in color_map_for(plot_df[path[0]]).items()}
    fig = px.treemap(plot_df, path=[px.Constant("All")] + path, values=value_col, color=path[0],
                     color_discrete_map={**cmap, "(?)": "#f3f4f6"})
    fig.update_traces(marker=dict(line=dict(color="white", width=2)), root_color="#f3f4f6",
                      texttemplate="%{label}<br>%{percentRoot:.1%}", textfont=dict(color=INK),
                      hovertemplate="%{label}<br>%{value:,.0f}<extra></extra>")
    return FigureResult("plotly", figure_to_json(_style(fig, 420, legend=False)))


def _gantt(chart: ChartConfig, df: pd.DataFrame, r) -> FigureResult:
    label = r(chart.label) or r(chart.x)
    start, end, color = r(chart.start), r(chart.end), r(chart.color)
    if not (label and start and end):
        date_cols = [c for c in df.columns if is_datetime(_maybe_datetime(df[c]))]
        if len(date_cols) >= 2 and not (start and end):
            start, end = date_cols[0], date_cols[1]
        if not label:
            label = next((c for c in df.columns if not is_numeric(df[c]) and c not in date_cols), "")
    if not (label and start and end):
        raise ChartError("A timeline needs a label column and start/end date columns.")
    plot_df = df.copy()
    plot_df[start] = pd.to_datetime(plot_df[start], errors="coerce")
    plot_df[end] = pd.to_datetime(plot_df[end], errors="coerce")
    plot_df = plot_df.dropna(subset=[start])
    if plot_df.empty:
        raise ChartError("No valid start dates.")
    plot_df[end] = plot_df[end].fillna(plot_df[start])
    same_day = plot_df[end] <= plot_df[start]
    plot_df.loc[same_day, end] = plot_df.loc[same_day, start] + pd.Timedelta(days=1)
    plot_df = plot_df.sort_values(start, kind="mergesort").head(80)
    plot_df[label] = plot_df[label].astype(str)
    if plot_df[label].duplicated().any():
        counts = plot_df.groupby(label).cumcount()
        plot_df[label] = plot_df[label] + counts.map(lambda n: "" if n == 0 else f" ({n + 1})")
    cmap = color_map_for(plot_df[color]) if color else None
    fig = px.timeline(plot_df, x_start=start, x_end=end, y=label, color=color or None, color_discrete_map=cmap,
                      labels={c: pretty(c) for c in plot_df.columns})
    if not color:
        fig.update_traces(marker_color=PALETTE[0])
    fig.update_yaxes(autorange="reversed", title_text="", showgrid=False)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, tickformat="%b %Y")
    fig.update_traces(marker_line_width=0)
    height = max(280, min(1200, 28 * plot_df[label].nunique() + 110))
    return FigureResult("plotly", figure_to_json(_style(fig, height, legend=bool(color))))


# --------------------------------------------------------------------------- #
# Fallback: pick a sensible chart from the shape of the data
# --------------------------------------------------------------------------- #
def suggest_chart(df: pd.DataFrame) -> ChartConfig | None:
    if df is None or df.empty:
        return None
    num = [c for c in df.columns if is_numeric(df[c])]
    dates = [c for c in df.columns if is_datetime(_maybe_datetime(df[c]))]
    cats = [c for c in df.columns if c not in num and c not in dates]
    if len(df) == 1 and num:
        return ChartConfig(type="kpi", y=num[:1])
    if dates and num:
        color = cats[0] if cats and df[cats[0]].nunique() <= MAX_SERIES else ""
        return ChartConfig(type="line", x=dates[0], y=num[:1], color=color)
    if cats and num and len(df) <= 60:
        return ChartConfig(type="bar", x=cats[0], y=num[:1], sort="desc", orientation="h" if len(df) > 8 else "v")
    if len(num) >= 2:
        return ChartConfig(type="scatter", x=num[0], y=[num[1]])
    return ChartConfig(type="table")


def render_with_fallback(chart: ChartConfig, df: pd.DataFrame, currency: str, max_table_rows: int) -> tuple[FigureResult, str]:
    """Render the requested chart; on failure fall back to a chart suggested by the data shape, then a table."""
    try:
        return build_figure(chart, df, currency, max_table_rows), ""
    except ChartError as exc:
        problem = str(exc)
    fallback = suggest_chart(df)
    if fallback and fallback.type != chart.type:
        try:
            fallback.value_format = chart.value_format
            res = build_figure(fallback, df, currency, max_table_rows)
            res.note = (res.note + " " if res.note else "") + f"Requested {chart.type} chart could not be drawn ({problem}); showing a {fallback.type} instead."
            return res, problem
        except ChartError:
            pass
    return FigureResult("table", table=frame_to_records(df, max_table_rows), note=f"Chart could not be drawn ({problem}); showing the data instead."), problem


def to_json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))
