import { useCallback, useEffect, useState } from "react";
import { api, runJob, type AskResult, type DashboardPayload, type InferResult } from "./api/client";
import type { AskHistoryItem, ChatTurn, CompatibilityReport, DashboardResult, DashboardSpec, DatasetInfo, SemanticModel } from "./api/types";
import { branding } from "./branding";
import { Banner, Progress } from "./components/Banner";
import Stepper from "./components/Stepper";
import { downloadSpec } from "./download";
import { useBusy } from "./hooks/useBusy";
import DashboardPage from "./pages/DashboardPage";
import DataPage from "./pages/DataPage";
import HomePage from "./pages/HomePage";
import ModelPage from "./pages/ModelPage";

type Stage = "home" | "data" | "model" | "dashboard";
const STAGE_INDEX: Record<Stage, number> = { home: 0, data: 1, model: 2, dashboard: 3 };

export default function App() {
  const [stage, setStage] = useState<Stage>("home");
  const [dataset, setDataset] = useState<DatasetInfo | null>(null);
  const [model, setModel] = useState<SemanticModel | null>(null);
  const [modelIntro, setModelIntro] = useState("");
  const [modelChat, setModelChat] = useState<ChatTurn[]>([]);
  const [spec, setSpec] = useState<DashboardSpec | null>(null);
  const [dashboard, setDashboard] = useState<DashboardResult | null>(null);
  const [dashWarnings, setDashWarnings] = useState<string[]>([]);
  const [dashChat, setDashChat] = useState<ChatTurn[]>([]);
  const [compat, setCompat] = useState<CompatibilityReport | null>(null);
  const [askItems, setAskItems] = useState<AskResult[]>([]);
  const [health, setHealth] = useState<{ version: string; llm: { configured: boolean; provider: string; model: string } } | null>(null);
  const [user, setUser] = useState<{ name: string; authenticated: boolean } | null>(null);
  const { busy, progress, error, setError, run } = useBusy();

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.me().then(setUser).catch(() => setUser(null));
  }, []);
  const llmConfigured = health?.llm.configured ?? false;

  const applyPayload = useCallback((payload: DashboardPayload) => {
    setSpec(payload.spec as DashboardSpec);
    setDashboard(payload.dashboard);
    setDashWarnings(payload.warnings ?? []);
  }, []);

  const reset = () => {
    setStage("home"); setDataset(null); setModel(null); setModelIntro(""); setModelChat([]); setSpec(null);
    setDashboard(null); setDashWarnings([]); setDashChat([]); setCompat(null); setAskItems([]); setError(null);
  };

  // ---- stage 1: upload ---------------------------------------------------
  const handleCreate = (file: File) => run("Reading the file…", async () => {
    const info = await api.uploadDataset(file);
    setDataset(info); setCompat(null); setStage("data");
  });
  const handleSheet = (sheet: string) => run("Reading the sheet…", async () => {
    if (!dataset) return;
    setDataset(await api.selectSheet(dataset.dataset_id, sheet));
  });

  // ---- stage 2: semantic model -------------------------------------------
  const handleAnalyse = () => run(llmConfigured ? "Asking the AI what your columns mean…" : "Analysing columns…", async (report) => {
    if (!dataset) return;
    const r = await runJob<InferResult>(() => api.startInfer(dataset.dataset_id), report);
    setModel(r.model); setModelIntro(r.reply); setModelChat([]); setStage("model");
  });
  const handleRefineModel = (message: string) => run("Updating the description…", async (report) => {
    if (!dataset || !model) return;
    const history = modelChat;
    setModelChat([...history, { role: "user", content: message }]);
    const r = await runJob<InferResult>(() => api.startRefineModel(dataset.dataset_id, model, message, history), report);
    setModel(r.model);
    setModelChat([...history, { role: "user", content: message }, { role: "assistant", content: r.reply }]);
  });

  // ---- stage 3: dashboard --------------------------------------------------
  const handleBuild = () => run(llmConfigured ? "Designing your dashboard…" : "Building a default dashboard…", async (report) => {
    if (!dataset || !model) return;
    const payload = await runJob<DashboardPayload>(() => api.startBuild(dataset.dataset_id, model), report);
    applyPayload(payload); setDashChat([]); setAskItems([]); setCompat(null); setStage("dashboard");
  });
  const handleRefineDashboard = (message: string) => run("Applying your change…", async (report) => {
    if (!dataset || !spec) return;
    const history = dashChat;
    setDashChat([...history, { role: "user", content: message }]);
    const payload = await runJob<DashboardPayload>(() => api.startRefineDashboard(dataset.dataset_id, spec, message, history), report);
    applyPayload(payload);
    const ch = payload.changes ?? {};
    const summary = [ch.added?.length ? `Added: ${ch.added.join(", ")}.` : "", ch.removed?.length ? `Removed: ${ch.removed.join(", ")}.` : "", ch.modified?.length ? `Changed: ${ch.modified.join(", ")}.` : ""].filter(Boolean).join(" ");
    setDashChat([...history, { role: "user", content: message }, { role: "assistant", content: `${payload.reply}${summary ? `\n\n_${summary}_` : ""}` }]);
  });
  const handleSpecEdit = (next: DashboardSpec) => run("Updating…", async () => {
    if (!dataset) return;
    applyPayload(await api.render(dataset.dataset_id, next));
  });
  const handleNarratives = (ids: string[] | null, force: boolean) => run("Writing summaries…", async (report) => {
    if (!dataset || !spec) return;
    applyPayload(await runJob<DashboardPayload>(() => api.startNarratives(dataset.dataset_id, spec, ids, force), report));
  });
  const handleDownload = () => { if (spec) downloadSpec(spec); };

  // ---- replay ----------------------------------------------------------------
  const handleReplay = (file: File, specFile: File) => run("Rebuilding your dashboard…", async () => {
    let parsed: unknown;
    try { parsed = JSON.parse(await specFile.text()); } catch { throw new Error("The dashboard file is not valid JSON. Use a file downloaded from this app."); }
    const r = await api.replay(file, parsed);
    setDataset(r.dataset); setSpec(r.spec as DashboardSpec); setModel((r.spec as DashboardSpec).semantic_model ?? null);
    setDashboard(r.dashboard); setDashWarnings(r.warnings ?? []); setCompat(r.compatibility); setDashChat([]); setAskItems([]);
    setStage("dashboard");
  });

  // ---- ask ---------------------------------------------------------------------
  const handleAsk = (question: string) => run("Working on your question…", async (report) => {
    if (!dataset || !model) return;
    const history: AskHistoryItem[] = askItems.map((a) => ({ question: a.question, summary: a.summary ?? "" }));
    const r = await runJob<AskResult>(() => api.startAsk(dataset.dataset_id, question, model, history), report);
    setAskItems((items) => [...items, r]);
  });
  const handleAddToDashboard = (item: AskResult) => {
    if (!spec || !item.widget) return;
    const ids = new Set((spec.widgets ?? []).map((w) => w.id));
    let id = item.widget.id ?? "question";
    let n = 2;
    while (ids.has(id)) id = `${item.widget.id}_${n++}`;
    handleSpecEdit({ ...spec, widgets: [...(spec.widgets ?? []), { ...item.widget, id }] });
  };

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={reset} aria-label="Home">
          {branding.logo ? <img src={branding.logo} alt="" style={{ height: branding.logoHeight }} /> : <span className="brand-mark" aria-hidden />}
          {branding.appName}
        </button>
        <Stepper current={STAGE_INDEX[stage]} />
        <div className="topbar-right muted small">
          {user?.authenticated ? user.name : ""}
          {health && <span title={`API v${health.version}`}>{llmConfigured ? ` · AI: ${health.llm.model}` : " · AI off"}</span>}
        </div>
      </header>

      <main>
        {error && <Banner kind="error" onClose={() => setError(null)}>{error}</Banner>}
        {busy && <Progress label={busy} progress={progress} />}
        {stage === "home" && <HomePage onCreate={handleCreate} onReplay={handleReplay} disabled={!!busy} llmConfigured={llmConfigured} />}
        {stage === "data" && dataset && <DataPage dataset={dataset} onSheet={handleSheet} onAnalyse={handleAnalyse} onBack={reset} disabled={!!busy} />}
        {stage === "model" && dataset && model && (
          <ModelPage dataset={dataset} model={model} setModel={setModel} intro={modelIntro} chat={modelChat} onSend={handleRefineModel}
            onBuild={handleBuild} onBack={() => setStage("data")} disabled={!!busy} llmConfigured={llmConfigured} />
        )}
        {stage === "dashboard" && spec && dashboard && (
          <DashboardPage spec={spec} dashboard={dashboard} warnings={dashWarnings} compatibility={compat} chat={dashChat}
            onSend={handleRefineDashboard} onSpecEdit={handleSpecEdit} onNarratives={handleNarratives} onDownload={handleDownload}
            onReset={reset} askItems={askItems} onAsk={handleAsk} onAddToDashboard={handleAddToDashboard} disabled={!!busy} llmConfigured={llmConfigured} />
        )}
      </main>
      <footer className="footer muted small">
        {branding.organization && (
          <span>{branding.organizationUrl ? <a href={branding.organizationUrl} target="_blank" rel="noreferrer">{branding.organization}</a> : branding.organization} · </span>
        )}
        {branding.footerNote}
        {branding.supportUrl && <span> · <a href={branding.supportUrl}>{branding.supportLabel}</a></span>}
      </footer>
    </div>
  );
}
