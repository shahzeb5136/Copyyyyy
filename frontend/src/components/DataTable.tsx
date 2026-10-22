type Table = { columns: string[]; rows: unknown[][]; truncated?: boolean; row_count?: number };

function isYearLike(rows: unknown[][], j: number): boolean {
  const vals = rows.map((r) => r[j]).filter((v) => typeof v === "number") as number[];
  return vals.length > 0 && vals.every((v) => Number.isInteger(v) && v >= 1900 && v <= 2100);
}

function fmt(v: unknown, plain = false): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") {
    if (plain) return String(v);
    return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}

export default function DataTable({ table, maxRows = 200 }: { table: Table; maxRows?: number }) {
  const rows = table.rows.slice(0, maxRows);
  const plain = table.columns.map((_, j) => isYearLike(rows, j));
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>{table.columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className={typeof v === "number" ? "num" : ""}>{fmt(v, plain[j])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {(table.truncated || table.rows.length > maxRows) && (
        <div className="muted small">Showing {rows.length.toLocaleString()} of {(table.row_count ?? table.rows.length).toLocaleString()} rows.</div>
      )}
    </div>
  );
}
