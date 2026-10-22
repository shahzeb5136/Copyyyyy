import type { DashboardSpec } from "./api/types";

function slug(text: string): string {
  return (text || "dashboard").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "dashboard";
}

/** Save the spec as a JSON file - this is the user's dashboard "file" they keep and re-upload later. */
export function downloadSpec(spec: DashboardSpec) {
  const name = `${slug(spec.metadata?.title ?? "dashboard")}.dashboard.json`;
  const blob = new Blob([JSON.stringify(spec, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadTableCsv(columns: string[], rows: unknown[][], title: string) {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [columns.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug(title)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
