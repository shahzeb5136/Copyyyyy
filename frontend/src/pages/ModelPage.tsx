import type { ChatTurn, ColumnSemantics, DatasetInfo, SemanticModel } from "../api/types";
import { AGGREGATIONS, DATA_TYPES, ROLES } from "../api/types";
import ChatPanel from "../components/ChatPanel";

type Props = {
  dataset: DatasetInfo;
  model: SemanticModel;
  setModel: (m: SemanticModel) => void;
  intro: string;
  chat: ChatTurn[];
  onSend: (message: string) => void;
  onBuild: () => void;
  onBack: () => void;
  disabled?: boolean;
  llmConfigured: boolean;
};

const SHAPES: { value: SemanticModel["data_shape"]; label: string; help: string }[] = [
  { value: "row_level", label: "Row-level records", help: "Each row is one transaction/record; charts must aggregate." },
  { value: "aggregated", label: "Already summarised", help: "The table already contains totals per period/category." },
  { value: "unclear", label: "Not sure", help: "Let the dashboard builder decide." },
];

export default function ModelPage({ dataset, model, setModel, intro, chat, onSend, onBuild, onBack, disabled, llmConfigured }: Props) {
  const update = (patch: Partial<SemanticModel>) => setModel({ ...model, ...patch });
  const updateCol = (i: number, patch: Partial<ColumnSemantics>) => {
    const columns = (model.columns ?? []).map((c, j) => (j === i ? { ...c, ...patch } : c));
    setModel({ ...model, columns });
  };
  const dateCols = (model.columns ?? []).filter((c) => c.data_type === "date" || c.data_type === "datetime");

  return (
    <div className="page model-page">
      <div className="page-head">
        <div>
          <h1>Describe the data</h1>
          <p className="muted">Check what the AI inferred. Edit anything directly, or explain it in the chat - then build the dashboard.</p>
        </div>
        <div className="actions">
          <button className="btn" onClick={onBack} disabled={disabled}>← Back</button>
          <button className="btn primary" onClick={onBuild} disabled={disabled}>Build dashboard →</button>
        </div>
      </div>

      <div className="model-layout">
        <div className="model-main">
          <section className="card">
            <header className="card-head">
              <h3>About this dataset</h3>
              <span className={`badge conf-${model.confidence ?? "medium"}`}>AI confidence: {model.confidence ?? "medium"}</span>
            </header>
            <div className="form-grid">
              <label>Title<input value={model.title ?? ""} onChange={(e) => update({ title: e.target.value })} disabled={disabled} /></label>
              <label>Domain<input value={model.domain ?? ""} onChange={(e) => update({ domain: e.target.value })} placeholder="finance, hr, sales…" disabled={disabled} /></label>
              <label className="span2">Description<textarea rows={2} value={model.description ?? ""} onChange={(e) => update({ description: e.target.value })} disabled={disabled} /></label>
              <label className="span2">One row represents<input value={model.grain ?? ""} onChange={(e) => update({ grain: e.target.value })} placeholder="e.g. one order line / one department per month" disabled={disabled} /></label>
              <label>Main date column
                <select value={model.time_column ?? ""} onChange={(e) => update({ time_column: e.target.value })} disabled={disabled}>
                  <option value="">(none)</option>
                  {dateCols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
              </label>
              <label>Currency<input value={model.currency ?? ""} onChange={(e) => update({ currency: e.target.value.toUpperCase() })} placeholder="AED, USD…" disabled={disabled} /></label>
            </div>
            <fieldset className="shape">
              <legend>What kind of table is this?</legend>
              {SHAPES.map((s) => (
                <label key={s.value} className="radio">
                  <input type="radio" name="shape" checked={model.data_shape === s.value} onChange={() => update({ data_shape: s.value })} disabled={disabled} />
                  <span><strong>{s.label}</strong> <span className="muted small">{s.help}</span></span>
                </label>
              ))}
              {model.data_shape_reasoning && <p className="muted small">AI reasoning: {model.data_shape_reasoning}</p>}
              <p className="muted small">{dataset.row_count.toLocaleString()} rows in the file.</p>
            </fieldset>
          </section>

          <section className="card">
            <header className="card-head"><h3>Columns</h3><span className="muted small">{model.columns?.length ?? 0} columns</span></header>
            <div className="table-wrap">
              <table className="col-table">
                <thead>
                  <tr><th>Column</th><th>Type</th><th>Role</th><th>Unit</th><th>Aggregate</th><th>Description</th></tr>
                </thead>
                <tbody>
                  {(model.columns ?? []).map((c, i) => (
                    <tr key={c.name} className={c.role === "ignore" ? "ignored" : ""}>
                      <td className="col-name"><strong>{c.name}</strong></td>
                      <td>
                        <select value={c.data_type ?? "text"} onChange={(e) => updateCol(i, { data_type: e.target.value as ColumnSemantics["data_type"] })} disabled={disabled}>
                          {DATA_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </td>
                      <td>
                        <select value={c.role ?? "attribute"} onChange={(e) => updateCol(i, { role: e.target.value as ColumnSemantics["role"] })} disabled={disabled}>
                          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                        </select>
                      </td>
                      <td><input className="narrow" value={c.unit ?? ""} onChange={(e) => updateCol(i, { unit: e.target.value })} placeholder="USD, %" disabled={disabled} /></td>
                      <td>
                        {c.role === "measure" ? (
                          <select value={c.aggregation ?? "sum"} onChange={(e) => updateCol(i, { aggregation: e.target.value as ColumnSemantics["aggregation"] })} disabled={disabled}>
                            {AGGREGATIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                          </select>
                        ) : <span className="muted small">–</span>}
                      </td>
                      <td className="col-desc"><textarea rows={2} value={c.description ?? ""} onChange={(e) => updateCol(i, { description: e.target.value })} disabled={disabled} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <aside className="model-side">
          {(model.open_questions?.length ?? 0) > 0 && (
            <section className="card questions">
              <header className="card-head"><h3>The AI would like to know</h3></header>
              <ul>{model.open_questions!.map((q, i) => <li key={i}>{q}</li>)}</ul>
              <p className="muted small">Answer in the chat below, or ignore and build.</p>
            </section>
          )}
          {(model.business_notes?.length ?? 0) > 0 && (
            <section className="card">
              <header className="card-head"><h3>Business rules</h3></header>
              <ul className="tight">
                {model.business_notes!.map((n, i) => (
                  <li key={i}>{n} <button className="link-btn small" disabled={disabled} onClick={() => update({ business_notes: model.business_notes!.filter((_, j) => j !== i) })}>remove</button></li>
                ))}
              </ul>
            </section>
          )}
          <ChatPanel
            title="Tell the AI about your data"
            intro={intro}
            turns={chat}
            onSend={onSend}
            disabled={disabled}
            placeholder={llmConfigured ? "e.g. 'Budget is in thousands of AED', 'Only closed periods count', 'cost_center is the department'…" : "AI is not configured; notes are saved as business rules."}
            suggestions={llmConfigured ? ["Amounts are in thousands", "Ignore the ID columns in charts", "The main date column is the invoice date"] : []}
          />
        </aside>
      </div>
    </div>
  );
}
