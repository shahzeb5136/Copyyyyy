"""Turn a chart *specification* (chosen by the visualization agent) into a Plotly figure.

The LLM never writes plotting code - it returns a small JSON spec (chart type +
which result columns go where). This module validates the spec against the
actual result, repairs what it safely can, and renders a consistently styled
figure. That keeps the app safe (no ``exec`` of model output) and the visuals
consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Validated, colour-blind-safe categorical palette (fixed order; colour follows the entity).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
DIVERGING = [[0.0, "#c13b3b"], [0.25, "#e88a86"], [0.5, "#f0efec"], [0.75, "#86b6ef"], [1.0, "#1c5cab"]]
NEUTRAL = "#8b8a85"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
          "info": "#2a78d6", "neutral": "#a3a29c"}
STATUS_WORDS = {
    "on track": "good", "completed": "good", "complete": "good", "closed": "good", "done": "good",
    "green": "good", "approved": "good", "active": "info", "in progress": "info",
    "at risk": "warning", "amber": "warning", "pending": "warning",
    "off track": "critical", "blocked": "critical", "overdue": "critical", "red": "critical",
    "cancelled": "neutral", "not started": "neutral", "on hold": "neutral", "open": "neutral",
}

FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
INK = "#1f2937"
INK_MUTED = "#6b7280"
GRID = "#eceef2"
AXIS = "#d6d9de"
MAX_CATEGORIES = 30
MAX_SERIES = 8

CURRENCY_PREFIX = {"USD": "$", "EUR": "€", "GBP": "£", "AED": "AED ", "SAR": "SAR ", "INR": "₹", "JPY": "¥"}


class ChartError(Exception):
    pass


@dataclass
class ChartSpec:
    chart_type: str = "bar"
    title: str = ""
    x: str = ""
    y: list[str] = field(default_factory=list)
    color: str = ""
    size: str = ""
    z: str = ""
    path: list[str] = field(default_factory=list)
    label: str = ""
    start: str = ""
    end: str = ""
    orientation: str = "v"
    barmode: str = "group"
    sort: str = "none"
    value_format: str = "number"

    @classmethod
    def from_dict(cls, d: dict) -> ChartSpec:
        known = {f.name for f in fields(cls)}
        clean = {}
        for k, v in (d or {}).items():
            if k not in known:
                continue
            if k in ("y", "path"):
                if isinstance(v, str):
                    v = [v] if v else []
                v = [str(i) for i in (v or []) if str(i).strip()]
            elif v is None:
                v = ""
            else:
                v = str(v).strip()
            clean[k] = v
        spec = cls(**clean)
        spec.chart_type = spec.chart_type.lower()
        spec.orientation = spec.orientation if spec.orientation in ("v", "h") else "v"
        spec.barmode = spec.barmode if spec.barmode in ("group", "stack") else "group"
        spec.sort = spec.sort if spec.sort in ("none", "asc", "desc") else "none"
        if spec.value_format not in ("number", "currency", "percent"):
            spec.value_format = "number"
        return spec

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class RenderedChart:
    kind: str  # "plotly" | "kpi"
    title: str
    spec: ChartSpec
    figure: go.Figure | None = None
    metrics: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def pretty(name: str) -> str:
    """'total_actual_usd' -> 'Total actual (USD)'."""
    words = str(name).replace("_", " ").strip().split()
    unit = ""
    if words and words[-1].lower() in {"usd", "eur", "gbp", "aed", "sar", "inr"}:
        unit = f" ({words.pop().upper()})"
    elif words and words[-1].lower() in {"pct", "percent", "percentage"}:
        words.pop()
        unit = " (%)"
    text = " ".join(words) or str(name)
    return text[:1].upper() + text[1:] + unit


def is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def is_datetime(s: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(s)


def _maybe_datetime(s: pd.Series) -> pd.Series:
    """Convert ISO-looking strings to datetimes; leave everything else alone."""
    if is_datetime(s) or is_numeric(s):
        return s
    try:
        sample = s.dropna().astype(str).head(50)
        if len(sample) and sample.str.match(r"^\d{4}-\d{2}(-\d{2})?").all():
            return pd.to_datetime(s, errors="coerce")
    except Exception:
        pass
    return s


def _resolver(df: pd.DataFrame):
    lookup = {c.lower().strip(): c for c in df.columns}

    def resolve(name: str) -> str:
        if not name:
            return ""
        return lookup.get(str(name).lower().strip().strip('"'), "")

    return resolve


def _is_fraction(values: pd.Series) -> bool:
    v = pd.to_numeric(values, errors="coerce").dropna()
    return len(v) > 0 and v.abs().max() <= 1.5


def _axis_format(values: pd.Series, value_format: str, currency: str) -> dict:
    """Plotly axis kwargs for tick + hover formatting."""
    v = pd.to_numeric(values, errors="coerce").dropna()
    big = len(v) and v.abs().max() >= 10_000
    if value_format == "percent":
        if _is_fraction(v):
            return {"tickformat": ".0%", "hoverformat": ".1%"}
        return {"ticksuffix": "%", "tickformat": ",.0f" if big else ",.3~f", "hoverformat": ",.1f"}
    prefix = CURRENCY_PREFIX.get(currency.upper(), f"{currency} ") if value_format == "currency" and currency else ""
    return {"tickprefix": prefix, "tickformat": "~s" if big else ",.4~f",
            "hoverformat": ",.0f" if big else ",.2f"}


def format_value(value, value_format: str = "number", currency: str = "USD", column: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return "—"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d %b %Y")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return str(value)
    if value_format == "percent" or column.lower().endswith(("_pct", "_percent", "_rate")):
        return f"{value:.1%}" if abs(value) <= 1.5 and not column.lower().endswith("_pct") else f"{value:,.1f}%"
    prefix = ""
    if value_format == "currency" or column.lower().endswith(("_usd", "_eur", "_gbp", "_aed")):
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
    """Mix a hex colour with white (amount 0 = original, 1 = white)."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (round(c + (255 - c) * amount) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def color_map_for(values) -> dict[str, str]:
    """Status vocabulary gets status colours; anything else gets the categorical palette in fixed order."""
    uniq = [v for v in pd.unique(pd.Series(list(values)).dropna().astype(str))]
    lowered = [u.lower() for u in uniq]
    if uniq and all(u in STATUS_WORDS for u in lowered):
        roles = [STATUS_WORDS[u] for u in lowered]
        if len(set(roles)) == len(roles):  # only when every value gets a distinct colour
            return {u: STATUS[r] for u, r in zip(uniq, roles)}
    return {u: PALETTE[i % len(PALETTE)] for i, u in enumerate(uniq)}


def _fold_series(df: pd.DataFrame, by: str, value_cols: list[str], keys: list[str], keep: int) -> tuple[pd.DataFrame, str]:
    """Keep the `keep` largest groups of `by`, fold the rest into 'Other' (summed per key)."""
    if df[by].nunique() <= keep + 1:
        return df, ""
    totals = df.groupby(by, dropna=False)[value_cols[0]].sum().abs().sort_values(ascending=False)
    top = set(totals.index[:keep])
    out = df.copy()
    out[by] = out[by].where(out[by].isin(top), "Other")
    out = out.groupby(keys + [by], dropna=False, sort=False)[value_cols].sum().reset_index()
    return out, f"{len(totals) - keep} smaller groups folded into 'Other'."


def _style(fig: go.Figure, height: int = 420, legend: bool = True) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        font=dict(family=FONT, size=13, color=INK),
        margin=dict(l=8, r=16, t=36 if legend else 12, b=8),
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=PALETTE,
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, title_text="",
                    font=dict(size=12, color=INK_MUTED), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="white", bordercolor=AXIS, font=dict(family=FONT, size=12, color=INK)),
        bargap=0.28,
        bargroupgap=0.06,
        barcornerradius=4,
        title=None,
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, ticks="", tickfont=dict(color=INK_MUTED),
                     title_font=dict(size=12, color=INK_MUTED), zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, linecolor=AXIS, showline=False, ticks="",
                     tickfont=dict(color=INK_MUTED), title_font=dict(size=12, color=INK_MUTED),
                     zeroline=True, zerolinecolor=AXIS, zerolinewidth=1, automargin=True)
    fig.update_traces(selector=dict(type="bar"), marker_line_width=0)
    return fig


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def build_chart(spec: ChartSpec, df: pd.DataFrame, currency: str = "USD",
                reference_date: str | None = None) -> RenderedChart:
    if df is None or df.empty:
        raise ChartError("No rows to plot.")
    df = df.copy()
    r = _resolver(df)
    t = spec.chart_type
    x, color, size, z = r(spec.x), r(spec.color), r(spec.size), r(spec.z)
    ys = [c for c in (r(y) for y in spec.y) if c]
    numeric_ys = [c for c in ys if is_numeric(df[c])]
    title = spec.title or "Result"
    note = ""

    if t == "kpi":
        if len(df) > 3:
            raise ChartError("KPI tiles need a 1-row result.")
        cols = numeric_ys or [c for c in df.columns if is_numeric(df[c])]
        if not cols:
            raise ChartError("No numeric values for KPI tiles.")
        row = df.iloc[0]
        metrics = [(pretty(c), format_value(row[c], spec.value_format if c in numeric_ys else "number", currency, c))
                   for c in cols[:4]]
        return RenderedChart("kpi", title, spec, metrics=metrics)

    if t == "gantt":
        return _gantt(spec, df, r, title, reference_date)

    if t in ("bar", "line", "area", "pie", "funnel", "waterfall"):
        if not x:
            raise ChartError(f"'{spec.x}' is not a column in the result.")
        if not numeric_ys:
            numeric_ys = [c for c in df.columns if c != x and is_numeric(df[c])][:1]
            if not numeric_ys:
                raise ChartError("No numeric column to plot.")
        if t in ("line", "area"):
            df[x] = _maybe_datetime(df[x])
        if color == x:
            color = ""

    labels = {c: pretty(c) for c in df.columns}
    fmt = spec.value_format

    # ---- bar ------------------------------------------------------------- #
    if t == "bar":
        plot_df, measure_col, value_col = df, None, numeric_ys[0]
        if len(numeric_ys) > 1:
            measure_col, value_col = "Measure", "__value"
            plot_df = df.melt(id_vars=[x], value_vars=numeric_ys, var_name="Measure", value_name="__value")
            plot_df["Measure"] = plot_df["Measure"].map(pretty)
            labels["__value"] = "Value"
            color = measure_col
        elif color:
            if df[color].nunique() > MAX_SERIES:
                plot_df, note = _fold_series(df, color, [value_col], [x], MAX_SERIES - 1)
        # Too many categories: keep the largest
        cats = plot_df.groupby(x, sort=False)[value_col].sum()
        if len(cats) > MAX_CATEGORIES:
            keep = cats.abs().sort_values(ascending=False).index[:MAX_CATEGORIES]
            plot_df = plot_df[plot_df[x].isin(keep)]
            note = f"Showing the {MAX_CATEGORIES} largest of {len(cats)} categories."
            cats = cats[cats.index.isin(keep)]
        order = list(cats.index)
        if spec.sort in ("asc", "desc"):
            order = list(cats.sort_values(ascending=spec.sort == "asc").index)
        # Plotly Express lists categories top-to-bottom on a horizontal bar chart, so the
        # largest bar ends up on top without reversing.
        horizontal = spec.orientation == "h" or (len(order) > 10 and not is_datetime(plot_df[x]))
        cmap = color_map_for(plot_df[color]) if color else None
        orders = {x: order} if not is_datetime(plot_df[x]) else {}
        if horizontal and color and spec.barmode == "group":
            # Grouped horizontal bars draw the first series at the bottom of each group;
            # reverse the series so they read top-down in legend order.
            orders[color] = list(pd.unique(plot_df[color].dropna()))[::-1]
        common = dict(color=color or None, color_discrete_map=cmap, labels=labels,
                      category_orders=orders or None, barmode=spec.barmode)
        if horizontal:
            fig = px.bar(plot_df, y=x, x=value_col, orientation="h", **common)
            fig.update_xaxes(**_axis_format(plot_df[value_col], fmt, currency), showgrid=True, gridcolor=GRID)
            fig.update_yaxes(showgrid=False, title_text="" if len(order) > 3 else pretty(x))
            height = max(320, min(900, 34 * len(order) * (1 if spec.barmode == "stack" or not color else 1.4) + 90))
        else:
            fig = px.bar(plot_df, x=x, y=value_col, **common)
            fig.update_yaxes(**_axis_format(plot_df[value_col], fmt, currency))
            height = 420
        if not color:
            fig.update_traces(marker_color=PALETTE[0])
        if measure_col:  # the value axis mixes several measures, so its title would mislead
            (fig.update_xaxes if horizontal else fig.update_yaxes)(title_text="")
        fig = _style(fig, height, legend=bool(color))
        n_cats = len(order)
        fig.update_layout(bargap=0.55 if n_cats <= 3 else 0.42 if n_cats <= 6 else 0.3)
        if horizontal and color and spec.barmode == "group":
            fig.update_layout(legend_traceorder="reversed")
        return RenderedChart("plotly", title, spec, fig, note=note)

    # ---- line / area ------------------------------------------------------ #
    if t in ("line", "area"):
        plot_df = df.sort_values(x)
        value_col = numeric_ys[0]
        if len(numeric_ys) > 1:
            plot_df = plot_df.melt(id_vars=[x], value_vars=numeric_ys[:4], var_name="Measure", value_name="__value")
            plot_df["Measure"] = plot_df["Measure"].map(pretty)
            color, value_col = "Measure", "__value"
            labels["__value"] = "Value"
        elif color and plot_df[color].nunique() > MAX_SERIES:
            plot_df, note = _fold_series(plot_df, color, [value_col], [x], MAX_SERIES - 1)
            plot_df = plot_df.sort_values(x)
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
        fig.update_yaxes(**_axis_format(plot_df[value_col], fmt, currency))
        fig.update_layout(hovermode="x unified")
        if value_col == "__value":
            fig.update_yaxes(title_text="")
        return RenderedChart("plotly", title, spec, _style(fig, 420, legend=bool(color)), note=note)

    # ---- pie (donut) -------------------------------------------------------- #
    if t == "pie":
        value_col = numeric_ys[0]
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
        fig = _style(fig, 400, legend=True)
        fig.update_layout(legend=dict(orientation="v", y=0.5, yanchor="middle", x=1.02, xanchor="left"),
                          margin=dict(l=8, r=8, t=24, b=24))
        return RenderedChart("plotly", title, spec, fig, note=note)

    # ---- funnel --------------------------------------------------------------- #
    if t == "funnel":
        value_col = numeric_ys[0]
        fig = px.funnel(df, x=value_col, y=x, labels=labels)
        fig.update_traces(marker_color=PALETTE[0], textinfo="value+percent initial",
                          connector=dict(fillcolor="#eef3fb"))
        return RenderedChart("plotly", title, spec, _style(fig, max(320, 60 * len(df) + 80), legend=False))

    # ---- waterfall ------------------------------------------------------------ #
    if t == "waterfall":
        value_col = numeric_ys[0]
        steps = df[[x, value_col]].dropna()
        if len(steps) > MAX_CATEGORIES:
            raise ChartError("Too many steps for a waterfall.")
        xs = steps[x].astype(str).tolist() + ["Total"]
        ys_ = steps[value_col].tolist() + [None]
        fig = go.Figure(go.Waterfall(
            x=xs, y=ys_, measure=["relative"] * len(steps) + ["total"],
            increasing=dict(marker=dict(color=PALETTE[0])),
            decreasing=dict(marker=dict(color="#e34948")),
            totals=dict(marker=dict(color=NEUTRAL)),
            connector=dict(line=dict(color=AXIS, width=1)),
        ))
        fig.update_yaxes(**_axis_format(steps[value_col], fmt, currency))
        return RenderedChart("plotly", title, spec, _style(fig, 420, legend=False))

    # ---- scatter ----------------------------------------------------------- #
    if t == "scatter":
        xcol = x if x and is_numeric(df[x]) else ""
        ycol = numeric_ys[0] if numeric_ys else ""
        if not xcol or not ycol:
            raise ChartError("A scatter plot needs two numeric columns.")
        if color and df[color].nunique() > 6:
            color = ""
            note = "Too many groups to colour individually."
        text_cols = [c for c in df.columns if not is_numeric(df[c]) and c != color]
        cmap = color_map_for(df[color]) if color else None
        fig = px.scatter(df, x=xcol, y=ycol, color=color or None, color_discrete_map=cmap,
                         size=size if size and is_numeric(df[size]) else None,
                         hover_name=text_cols[0] if text_cols else None, labels=labels)
        fig.update_traces(marker=dict(line=dict(width=1.5, color="white"), opacity=0.9,
                                      **({} if size else {"size": 10})))
        if not color:
            fig.update_traces(marker_color=PALETTE[0])
        fig.update_xaxes(**_axis_format(df[xcol], "number", currency), showgrid=True, gridcolor=GRID)
        fig.update_yaxes(**_axis_format(df[ycol], fmt, currency))
        return RenderedChart("plotly", title, spec, _style(fig, 440, legend=bool(color)), note=note)

    # ---- histogram ----------------------------------------------------------- #
    if t == "histogram":
        col = x if x and is_numeric(df[x]) else (numeric_ys[0] if numeric_ys else "")
        if not col:
            raise ChartError("A histogram needs a numeric column.")
        if color and df[color].nunique() > 4:
            color = ""
        fig = px.histogram(df, x=col, color=color or None, labels=labels,
                           color_discrete_map=color_map_for(df[color]) if color else None,
                           barmode="overlay" if color else "relative", opacity=0.85 if color else 1)
        if not color:
            fig.update_traces(marker_color=PALETTE[0])
        fig.update_xaxes(**_axis_format(df[col], fmt, currency))
        fig.update_yaxes(title_text="Count")
        fig.update_layout(bargap=0.06)
        return RenderedChart("plotly", title, spec, _style(fig, 400, legend=bool(color)))

    # ---- box ------------------------------------------------------------------- #
    if t == "box":
        ycol = numeric_ys[0] if numeric_ys else ""
        if not ycol:
            raise ChartError("A box plot needs a numeric column.")
        fig = px.box(df, x=x or None, y=ycol, labels=labels, points="outliers")
        fig.update_traces(marker_color=PALETTE[0], line_color=PALETTE[0], fillcolor="rgba(42,120,214,0.15)")
        fig.update_yaxes(**_axis_format(df[ycol], fmt, currency))
        return RenderedChart("plotly", title, spec, _style(fig, 420, legend=False))

    # ---- heatmap ---------------------------------------------------------------- #
    if t == "heatmap":
        row_dim = ys[0] if ys else ""
        if row_dim and is_numeric(df[row_dim]) and not z:
            z, row_dim = row_dim, ""
        if not row_dim:
            row_dim = next((c for c in df.columns if c not in (x, z) and not is_numeric(df[c])), "")
        if not (x and row_dim and z and is_numeric(df[z])):
            raise ChartError("A heatmap needs x, y and a numeric z column.")
        grid = df.pivot_table(index=row_dim, columns=x, values=z, aggfunc="sum")
        if grid.shape[0] > 40 or grid.shape[1] > 40:
            raise ChartError("Too many rows/columns for a heatmap.")
        if is_datetime(df[x]):
            grid.columns = [c.strftime("%Y-%m-%d") if isinstance(c, pd.Timestamp) else c for c in grid.columns]
        diverging = (grid.min().min() < 0) and (grid.max().max() > 0)
        fig = px.imshow(grid, aspect="auto", labels=dict(x=pretty(x), y=pretty(row_dim), color=pretty(z)),
                        color_continuous_scale=DIVERGING if diverging else SEQUENTIAL,
                        color_continuous_midpoint=0 if diverging else None, text_auto=False)
        cells = grid.shape[0] * grid.shape[1]
        if cells <= 120:
            text = grid.map(lambda v: "" if pd.isna(v) else format_value(v, fmt, currency, z))
            fig.update_traces(text=text.values, texttemplate="%{text}", textfont=dict(size=11))
        fig.update_xaxes(side="bottom", showgrid=False)
        fig.update_yaxes(showgrid=False, zeroline=False)
        fig.update_layout(coloraxis_colorbar=dict(thickness=12, title=dict(text="")))
        return RenderedChart("plotly", title, spec, _style(fig, max(320, 30 * grid.shape[0] + 140), legend=False))

    # ---- treemap ---------------------------------------------------------------- #
    if t == "treemap":
        path = [c for c in (r(p) for p in spec.path) if c] or ([x] if x else [])
        value_col = numeric_ys[0] if numeric_ys else ""
        if not path or not value_col:
            raise ChartError("A treemap needs category path columns and a value column.")
        plot_df = df[df[value_col] > 0].copy()
        for p in path:
            plot_df[p] = plot_df[p].fillna("(blank)").astype(str)
        # Large areas get tinted fills (saturated colour is for small marks) with dark labels.
        cmap = {k: _tint(v, 0.55) for k, v in color_map_for(plot_df[path[0]]).items()}
        fig = px.treemap(plot_df, path=[px.Constant("All")] + path, values=value_col, color=path[0],
                         color_discrete_map={**cmap, "(?)": "#f3f4f6"})
        fig.update_traces(marker=dict(line=dict(color="white", width=2)), root_color="#f3f4f6",
                          texttemplate="%{label}<br>%{percentRoot:.1%}", textfont=dict(color=INK),
                          hovertemplate="%{label}<br>%{value:,.0f}<extra></extra>")
        return RenderedChart("plotly", title, spec, _style(fig, 460, legend=False))

    raise ChartError(f"Unsupported chart type '{t}'.")


def _gantt(spec: ChartSpec, df: pd.DataFrame, r, title: str, reference_date: str | None) -> RenderedChart:
    label = r(spec.label) or r(spec.x)
    start, end, color = r(spec.start), r(spec.end), r(spec.color)
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
    plot_df.loc[same_day, end] = plot_df.loc[same_day, start] + pd.Timedelta(days=1)  # milestones stay visible
    plot_df = plot_df.sort_values(start).head(60)
    plot_df[label] = plot_df[label].astype(str)
    # Ensure unique row labels so bars don't stack on the same line.
    if plot_df[label].duplicated().any():
        counts = plot_df.groupby(label).cumcount()
        plot_df[label] = plot_df[label] + counts.map(lambda n: "" if n == 0 else f" ({n + 1})")
    cmap = color_map_for(plot_df[color]) if color else None
    fig = px.timeline(plot_df, x_start=start, x_end=end, y=label, color=color or None,
                      color_discrete_map=cmap, labels={c: pretty(c) for c in plot_df.columns},
                      hover_data={start: "|%d %b %Y", end: "|%d %b %Y"})
    if not color:
        fig.update_traces(marker_color=PALETTE[0])
    fig.update_yaxes(autorange="reversed", title_text="", showgrid=False)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, tickformat="%b %Y")
    if reference_date:
        ref = pd.Timestamp(reference_date)
        lo, hi = plot_df[start].min(), plot_df[end].max()
        if lo <= ref <= hi:
            fig.add_vline(x=ref, line_width=1.5, line_dash="dot", line_color=INK_MUTED)
            fig.add_annotation(x=ref, y=1.0, yref="paper", text="As of " + ref.strftime("%d %b %Y"),
                               showarrow=False, yanchor="bottom", font=dict(size=11, color=INK_MUTED))
    height = max(300, min(1400, 30 * plot_df[label].nunique() + 120))
    fig.update_traces(marker_line_width=0)
    return RenderedChart("plotly", title, spec, _style(fig, height, legend=bool(color)))


# --------------------------------------------------------------------------- #
# Fallback: pick a sensible chart from the shape of the data
# --------------------------------------------------------------------------- #
def suggest_spec(df: pd.DataFrame) -> ChartSpec | None:
    if df is None or df.empty:
        return None
    num = [c for c in df.columns if is_numeric(df[c])]
    dates = [c for c in df.columns if is_datetime(_maybe_datetime(df[c]))]
    cats = [c for c in df.columns if c not in num and c not in dates]

    def fmt_for(col: str) -> str:
        lc = col.lower()
        if lc.endswith(("_pct", "_percent", "_rate")):
            return "percent"
        if lc.endswith(("_usd", "_eur", "_gbp", "_aed", "_amount", "_cost", "_revenue")):
            return "currency"
        return "number"

    if len(df) == 1 and num:
        return ChartSpec("kpi", "Key figures", y=num[:4], value_format="number")
    if len(dates) >= 2 and cats:
        return ChartSpec("gantt", "Timeline", label=cats[0], start=dates[0], end=dates[1],
                         color=cats[1] if len(cats) > 1 and df[cats[1]].nunique() <= 8 else "")
    if dates and num:
        color = cats[0] if cats and df[cats[0]].nunique() <= MAX_SERIES else ""
        return ChartSpec("line", f"{pretty(num[0])} over time", x=dates[0], y=num[:1], color=color,
                         value_format=fmt_for(num[0]))
    if cats and num and len(df) <= 60:
        return ChartSpec("bar", f"{pretty(num[0])} by {pretty(cats[0]).lower()}", x=cats[0], y=num[:1],
                         sort="desc", orientation="h" if len(df) > 8 else "v", value_format=fmt_for(num[0]))
    if len(num) >= 2:
        return ChartSpec("scatter", f"{pretty(num[1])} vs {pretty(num[0]).lower()}", x=num[0], y=[num[1]])
    return None


def build_charts(specs: list[ChartSpec], df: pd.DataFrame, currency: str = "USD",
                 reference_date: str | None = None) -> tuple[list[RenderedChart], list[str]]:
    """Render every valid spec; fall back to a heuristic chart when none survive."""
    rendered, problems = [], []
    for spec in specs[:3]:
        try:
            rendered.append(build_chart(spec, df, currency, reference_date))
        except ChartError as exc:
            problems.append(f"{spec.chart_type}: {exc}")
        except Exception as exc:  # noqa: BLE001 - never let a chart crash the answer
            problems.append(f"{spec.chart_type}: unexpected rendering error ({type(exc).__name__}: {exc})")
    if not rendered and problems:
        fallback = suggest_spec(df)
        if fallback:
            try:
                rendered.append(build_chart(fallback, df, currency, reference_date))
                problems.append("Used an automatic fallback chart instead.")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"fallback: {exc}")
    return rendered, problems
