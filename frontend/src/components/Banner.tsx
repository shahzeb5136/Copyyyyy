import { useState, type ReactNode } from "react";

type Kind = "info" | "warn" | "error" | "success";

export function Banner({ kind = "info", children, onClose }: { kind?: Kind; children: ReactNode; onClose?: () => void }) {
  return (
    <div className={`banner banner-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <div className="banner-body">{children}</div>
      {onClose && <button className="icon-btn" onClick={onClose} aria-label="Dismiss">×</button>}
    </div>
  );
}

export function WarningList({ warnings, title = "Notes" }: { warnings: string[]; title?: string }) {
  const [open, setOpen] = useState(false);
  if (!warnings.length) return null;
  return (
    <div className="banner banner-warn">
      <div className="banner-body">
        <button className="link-btn" onClick={() => setOpen(!open)}>{title} ({warnings.length}) {open ? "▲" : "▼"}</button>
        {open && <ul className="tight">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      </div>
    </div>
  );
}

export function Progress({ label, progress }: { label: string; progress?: string }) {
  return (
    <div className="progress" role="status" aria-live="polite">
      <span className="spinner" aria-hidden />
      <div>
        <div className="progress-label">{label}</div>
        {progress && <div className="muted small">{progress}</div>}
      </div>
    </div>
  );
}
