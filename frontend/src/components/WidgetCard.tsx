import { useState } from "react";
import type { WidgetResult } from "../api/types";
import { downloadTableCsv } from "../download";
import DataTable from "./DataTable";
import Markdown from "./Markdown";
import PlotlyChart from "./PlotlyChart";

type Props = {
  widget: WidgetResult;
  index: number;
  count: number;
  onRemove?: () => void;
  onMove?: (delta: number) => void;
  onResize?: (w: number) => void;
  onRegenerate?: () => void;
  busy?: boolean;
};

export default function WidgetCard({ widget, index, count, onRemove, onMove, onResize, onRegenerate, busy }: Props) {
  const [showData, setShowData] = useState(false);
  const [menu, setMenu] = useState(false);
  const w = Math.min(12, Math.max(2, widget.w ?? 6));
  const table = (widget.table ?? null) as { columns: string[]; rows: unknown[][]; truncated?: boolean; row_count?: number } | null;
  const isKpi = widget.kind === "kpi";
  const editable = Boolean(onRemove || onMove || onResize);

  return (
    <article className={`widget ${isKpi ? "widget-kpi" : ""} status-${widget.status}`} style={{ gridColumn: `span ${w}` }}>
      <header className="widget-head">
        <div className="widget-titles">
          <h3>{widget.title || widget.id}</h3>
          {widget.subtitle && !isKpi && <div className="muted small">{widget.subtitle}</div>}
        </div>
        {editable && (
          <div className="widget-menu">
            <button className="icon-btn" aria-label="Widget options" onClick={() => setMenu(!menu)} disabled={busy}>⋯</button>
            {menu && (
              <div className="menu" onMouseLeave={() => setMenu(false)}>
                {onMove && index > 0 && <button onClick={() => { onMove(-1); setMenu(false); }}>Move up</button>}
                {onMove && index < count - 1 && <button onClick={() => { onMove(1); setMenu(false); }}>Move down</button>}
                {onResize && w < 12 && <button onClick={() => { onResize(Math.min(12, w + 3)); setMenu(false); }}>Wider</button>}
                {onResize && w > 3 && <button onClick={() => { onResize(Math.max(3, w - 3)); setMenu(false); }}>Narrower</button>}
                {table && <button onClick={() => { downloadTableCsv(table.columns, table.rows, widget.title ?? ""); setMenu(false); }}>Download data (CSV)</button>}
                {widget.kind === "plotly" && table && <button onClick={() => { setShowData(!showData); setMenu(false); }}>{showData ? "Show chart" : "Show data"}</button>}
                {onRegenerate && widget.kind === "text" && <button onClick={() => { onRegenerate(); setMenu(false); }}>Rewrite summary</button>}
                {onRemove && <button className="danger" onClick={() => { onRemove(); setMenu(false); }}>Remove</button>}
              </div>
            )}
          </div>
        )}
      </header>
      <div className="widget-body">
        {widget.status === "error" && <div className="widget-error">Could not build this chart: {widget.error}</div>}
        {widget.status === "empty" && <div className="widget-error muted">No data matches this chart's filters.</div>}
        {widget.status === "pending" && (
          <div className="muted">
            Summary not written yet.{onRegenerate && <> <button className="link-btn" onClick={onRegenerate} disabled={busy}>Write it now</button></>}
          </div>
        )}
        {widget.kind === "kpi" && widget.kpi && (
          <div className="kpi">
            <div className="kpi-value">{widget.kpi.value_formatted as string}</div>
            {widget.subtitle && <div className="muted small">{widget.subtitle}</div>}
          </div>
        )}
        {widget.kind === "plotly" && widget.figure && !showData && <PlotlyChart figure={widget.figure as { data: unknown[]; layout?: Record<string, unknown> }} />}
        {widget.kind === "plotly" && showData && table && <DataTable table={table} />}
        {widget.kind === "table" && table && <DataTable table={table} />}
        {widget.kind === "text" && widget.status === "ok" && (
          <div>
            {widget.stale && <span className="badge warn" title="The data changed after this summary was written">Written from previous data</span>}
            <Markdown text={widget.text ?? ""} />
          </div>
        )}
        {widget.note && <div className="muted small widget-note">{widget.note}</div>}
      </div>
    </article>
  );
}
