import { useState } from "react";
import type { AskResult, ChatTurn, CompatibilityReport, DashboardResult, DashboardSpec, Widget } from "../api/types";
import { Banner, WarningList } from "../components/Banner";
import ChatPanel from "../components/ChatPanel";
import WidgetCard from "../components/WidgetCard";
import AskPanel from "./AskPanel";

type Props = {
  spec: DashboardSpec;
  dashboard: DashboardResult;
  warnings: string[];
  compatibility: CompatibilityReport | null;
  chat: ChatTurn[];
  onSend: (message: string) => void;
  onSpecEdit: (spec: DashboardSpec) => void;
  onNarratives: (ids: string[] | null, force: boolean) => void;
  onDownload: () => void;
  onReset: () => void;
  askItems: AskResult[];
  onAsk: (q: string) => void;
  onAddToDashboard: (item: AskResult) => void;
  disabled?: boolean;
  llmConfigured: boolean;
};

export default function DashboardPage(p: Props) {
  const [tab, setTab] = useState<"dashboard" | "ask">("dashboard");
  const widgets = p.spec.widgets ?? [];
  const results = p.dashboard.widgets ?? [];
  const hasStale = results.some((w) => w.stale);
  const hasPending = results.some((w) => w.status === "pending");

  const edit = (fn: (ws: Widget[]) => Widget[]) => p.onSpecEdit({ ...p.spec, widgets: fn([...widgets]) });
  const remove = (id: string) => edit((ws) => ws.filter((w) => w.id !== id));
  const move = (id: string, delta: number) => edit((ws) => {
    const i = ws.findIndex((w) => w.id === id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ws.length) return ws;
    [ws[i], ws[j]] = [ws[j], ws[i]];
    return ws;
  });
  const resize = (id: string, w: number) => edit((ws) => ws.map((x) => (x.id === id ? { ...x, layout: { ...(x.layout ?? {}), w } } : x)));

  const measures = (p.spec.semantic_model?.columns ?? []).filter((c) => c.role === "measure").map((c) => c.name);
  const dims = (p.spec.semantic_model?.columns ?? []).filter((c) => c.role === "dimension").map((c) => c.name);
  const chartSuggestions = [
    dims[0] && measures[0] ? `Add a chart of ${measures[0]} by ${dims[0]}` : "Add a table of the top 10 rows",
    results.find((w) => w.kind === "plotly") ? `Add an executive summary under "${results.find((w) => w.kind === "plotly")!.title}"` : "Add an executive summary",
    "Show the trend by quarter instead of month",
  ];
  const askSuggestions = [
    measures[0] && dims[0] ? `Which ${dims[0]} has the highest ${measures[0]}?` : "What are the key figures?",
    measures[0] ? `How has ${measures[0]} changed over time?` : "How many rows are there?",
  ];

  return (
    <div className="page dashboard-page">
      <div className="page-head">
        <div>
          <h1>{p.dashboard.title || p.spec.metadata?.title || "Dashboard"}</h1>
          {p.dashboard.description && <p className="muted">{p.dashboard.description}</p>}
          <p className="muted small">
            {p.dashboard.source_rows.toLocaleString()} source rows · {results.filter((w) => w.type === "chart").length} charts
            {p.spec.metadata?.llm_model ? ` · designed with ${p.spec.metadata.llm_model}` : ""}
          </p>
        </div>
        <div className="actions">
          <button className="btn" onClick={p.onReset} disabled={p.disabled}>Start over</button>
          {(hasStale || hasPending) && p.llmConfigured && (
            <button className="btn" onClick={() => p.onNarratives(null, true)} disabled={p.disabled}>Refresh summaries</button>
          )}
          <button className="btn primary" onClick={p.onDownload} disabled={p.disabled}>⬇ Download dashboard file</button>
        </div>
      </div>

      {p.compatibility && (
        <Banner kind={p.compatibility.ok ? "success" : "warn"}>
          <strong>{p.compatibility.ok ? "Rebuilt from your saved dashboard." : "Rebuilt with differences."}</strong>{" "}
          {p.compatibility.messages?.length ? p.compatibility.messages.join(" ") : "The new file matches the original columns."}
          {!p.compatibility.ok && p.llmConfigured && <> Describe the change in the chat (e.g. "the column X was renamed to Y") to fix the affected charts.</>}
        </Banner>
      )}
      <WarningList warnings={p.warnings} />

      <nav className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "dashboard"} className={tab === "dashboard" ? "active" : ""} onClick={() => setTab("dashboard")}>Dashboard</button>
        <button role="tab" aria-selected={tab === "ask"} className={tab === "ask" ? "active" : ""} onClick={() => setTab("ask")}>Ask a question{p.askItems.length ? ` (${p.askItems.length})` : ""}</button>
      </nav>

      {tab === "dashboard" ? (
        <div className="dash-layout">
          <div className="grid">
            {results.map((w, i) => (
              <WidgetCard key={w.id} widget={w} index={i} count={results.length} busy={p.disabled}
                onRemove={() => remove(w.id)} onMove={(d) => move(w.id, d)} onResize={(width) => resize(w.id, width)}
                onRegenerate={w.kind === "text" || w.status === "pending" ? () => p.onNarratives([w.id], true) : undefined} />
            ))}
            {results.length === 0 && <div className="muted">The dashboard is empty. Ask for a chart in the chat.</div>}
          </div>
          <aside className="dash-side">
            <ChatPanel
              title="Change the dashboard"
              intro={p.llmConfigured ? "Tell me what to add, remove or change. For example: *add a chart of revenue by region*, *remove the pie chart*, *add an executive summary under the trend chart*, *make the first chart full width*." : "AI is not configured on this server. You can still remove, reorder and resize widgets from each widget's menu, and download the dashboard file."}
              turns={p.chat}
              onSend={p.onSend}
              disabled={p.disabled || !p.llmConfigured}
              placeholder="Describe the change you want…"
              suggestions={p.llmConfigured && p.chat.length === 0 ? chartSuggestions : []}
            />
          </aside>
        </div>
      ) : (
        <AskPanel items={p.askItems} onAsk={p.onAsk} onAddToDashboard={p.onAddToDashboard} disabled={p.disabled} llmConfigured={p.llmConfigured} suggestions={askSuggestions} />
      )}
    </div>
  );
}
