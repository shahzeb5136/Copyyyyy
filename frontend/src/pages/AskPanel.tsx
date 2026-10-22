import { useState } from "react";
import type { AskResult } from "../api/types";
import DataTable from "../components/DataTable";
import Markdown from "../components/Markdown";
import PlotlyChart from "../components/PlotlyChart";

type Props = {
  items: AskResult[];
  onAsk: (question: string) => void;
  onAddToDashboard: (item: AskResult) => void;
  disabled?: boolean;
  llmConfigured: boolean;
  suggestions: string[];
};

export default function AskPanel({ items, onAsk, onAddToDashboard, disabled, llmConfigured, suggestions }: Props) {
  const [text, setText] = useState("");
  const [showData, setShowData] = useState<Record<number, boolean>>({});
  const send = (q: string) => { const m = q.trim(); if (!m || disabled) return; setText(""); onAsk(m); };

  return (
    <div className="ask">
      <section className="card">
        <header className="card-head"><h3>Ask anything about this data</h3></header>
        <p className="muted small">The AI plans a calculation, the app runs it on your file, and the AI explains the result. You can add any answer to the dashboard.</p>
        <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} disabled={disabled || !llmConfigured}
            placeholder={llmConfigured ? "e.g. Which category is most over budget this year?" : "AI is not configured on this server."}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
          <button className="btn primary" type="submit" disabled={disabled || !llmConfigured || !text.trim()}>Ask</button>
        </form>
        {suggestions.length > 0 && items.length === 0 && (
          <div className="chips">{suggestions.map((s) => <button key={s} className="chip" disabled={disabled || !llmConfigured} onClick={() => send(s)}>{s}</button>)}</div>
        )}
      </section>

      {[...items].reverse().map((item, idx) => {
        const i = items.length - 1 - idx;
        return (
          <section key={i} className="card ask-item">
            <div className="ask-q">{item.question}</div>
            <Markdown text={item.answer ?? ""} />
            {item.assumptions && item.can_answer && <p className="muted small">Assumption: {item.assumptions}</p>}
            {item.kind === "kpi" && item.kpi && <div className="kpi inline"><div className="kpi-value">{String(item.kpi.value_formatted)}</div></div>}
            {item.kind === "plotly" && item.figure && !showData[i] && <PlotlyChart figure={item.figure as { data: unknown[]; layout?: Record<string, unknown> }} />}
            {(item.kind === "table" || showData[i]) && item.table && <DataTable table={item.table as { columns: string[]; rows: unknown[][]; truncated?: boolean; row_count?: number }} />}
            {item.note && <p className="muted small">{item.note}</p>}
            {item.can_answer && (
              <div className="actions left">
                {item.kind === "plotly" && item.table && <button className="btn small" onClick={() => setShowData({ ...showData, [i]: !showData[i] })}>{showData[i] ? "Show chart" : "Show data"}</button>}
                {item.widget && <button className="btn small" disabled={disabled} onClick={() => onAddToDashboard(item)}>+ Add to dashboard</button>}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
