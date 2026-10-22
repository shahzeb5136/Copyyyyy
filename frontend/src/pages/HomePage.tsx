import { useEffect, useState } from "react";
import { api } from "../api/client";
import FileDrop from "../components/FileDrop";

type Props = {
  onCreate: (file: File) => void;
  onReplay: (file: File, specFile: File) => void;
  disabled?: boolean;
  llmConfigured: boolean;
};

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xlsm";

export default function HomePage({ onCreate, onReplay, disabled, llmConfigured }: Props) {
  const [dataFile, setDataFile] = useState<File | null>(null);
  const [specFile, setSpecFile] = useState<File | null>(null);
  const [samples, setSamples] = useState<{ name: string; size: number }[]>([]);
  useEffect(() => { api.listSamples().then(setSamples).catch(() => setSamples([])); }, []);

  return (
    <div className="home">
      <section className="hero">
        <h1>Turn a spreadsheet into a dashboard</h1>
        <p className="lead">
          Upload a CSV or Excel file. The app works out what your columns mean, asks you to confirm, builds a dashboard,
          and lets you change it in plain English. Save the dashboard as a file and rebuild it any time with new data.
        </p>
        {!llmConfigured && (
          <p className="muted small">AI features are switched off on this server (no model key configured). You can still upload data and get an automatic dashboard.</p>
        )}
      </section>

      <div className="home-cards">
        <section className="card option">
          <h2>Create a new dashboard</h2>
          <p className="muted">Column names must be in the first row of the sheet.</p>
          <FileDrop accept={ACCEPT} label="Drop your CSV or Excel file here, or click to choose" hint="Up to 50 MB · .csv, .xlsx" onFile={(f) => { setDataFile(null); onCreate(f); }} />
          {samples.length > 0 && (
            <div className="samples">
              <span className="muted small">No file handy? Try a sample:</span>
              {samples.filter((s) => !s.name.endsWith(".json")).map((s) => (
                <button key={s.name} className="chip" disabled={disabled} onClick={async () => onCreate(await api.fetchSample(s.name))}>{s.name}</button>
              ))}
            </div>
          )}
        </section>

        <section className="card option">
          <h2>Rebuild a saved dashboard</h2>
          <p className="muted">Upload a new version of the data together with the dashboard file (.json) you downloaded earlier. No AI needed.</p>
          <FileDrop compact accept={ACCEPT} label="New data file (.csv / .xlsx)" hint="Same columns as the original" file={dataFile} onFile={setDataFile} />
          <FileDrop compact accept=".json" label="Dashboard file (.dashboard.json)" hint="Downloaded from this app" file={specFile} onFile={setSpecFile} />
          <button className="btn primary" disabled={disabled || !dataFile || !specFile} onClick={() => dataFile && specFile && onReplay(dataFile, specFile)}>
            Rebuild dashboard
          </button>
        </section>
      </div>

      <section className="how">
        <h2>How it works</h2>
        <ol className="how-steps">
          <li><strong>Check the data.</strong> We read the file and show you what we found: rows, columns, types.</li>
          <li><strong>Describe the columns.</strong> The AI proposes what each column means; you correct it and add business rules in the chat.</li>
          <li><strong>Get a dashboard.</strong> KPIs, trends and breakdowns, plus written summaries. Ask for changes in plain English.</li>
          <li><strong>Save and reuse.</strong> Download the dashboard file. Next month, upload it with the new data and it rebuilds instantly.</li>
        </ol>
        <p className="muted small">
          Privacy: only column names, summary statistics, the most common values and a handful of sample rows are sent to the AI model - never the whole file.
          Uploaded files are deleted automatically after a retention period.
        </p>
      </section>
    </div>
  );
}
