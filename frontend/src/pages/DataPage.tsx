import type { DatasetInfo } from "../api/types";
import { Banner } from "../components/Banner";
import DataTable from "../components/DataTable";

type Props = {
  dataset: DatasetInfo;
  onSheet: (sheet: string) => void;
  onAnalyse: () => void;
  onBack: () => void;
  disabled?: boolean;
};

export default function DataPage({ dataset, onSheet, onAnalyse, onBack, disabled }: Props) {
  const sheets = dataset.sheet_names ?? [];
  const warnings = dataset.warnings ?? [];
  const columns = dataset.columns ?? [];
  const multi = sheets.length > 1;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{dataset.file_name}</h1>
          <p className="muted">
            {dataset.row_count.toLocaleString()} rows · {dataset.column_count} columns{dataset.sheet ? ` · sheet "${dataset.sheet}"` : ""}
          </p>
        </div>
        <div className="actions">
          <button className="btn" onClick={onBack} disabled={disabled}>Choose another file</button>
          <button className="btn primary" onClick={onAnalyse} disabled={disabled}>Analyse columns →</button>
        </div>
      </div>

      {multi && (
        <div className="card row">
          <label htmlFor="sheet"><strong>Sheet:</strong></label>
          <select id="sheet" value={dataset.sheet} onChange={(e) => onSheet(e.target.value)} disabled={disabled}>
            {sheets.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <span className="muted small">The workbook has {sheets.length} sheets. Pick the one with your data.</span>
        </div>
      )}

      {warnings.length > 0 && (
        <Banner kind="warn">
          <ul className="tight">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </Banner>
      )}

      <section className="card">
        <header className="card-head"><h3>Columns we found</h3></header>
        <div className="col-chips">
          {columns.map((c) => (
            <span key={c.name} className="col-chip" title={`${(c.distinct ?? 0).toLocaleString()} distinct · ${(c.nulls ?? 0).toLocaleString()} blank · e.g. ${(c.samples ?? []).slice(0, 3).join(", ")}`}>
              <strong>{c.name}</strong> <em>{c.data_type}</em>
            </span>
          ))}
        </div>
      </section>

      <section className="card">
        <header className="card-head"><h3>First rows</h3></header>
        <DataTable table={dataset.preview as { columns: string[]; rows: unknown[][] }} maxRows={10} />
      </section>
    </div>
  );
}
