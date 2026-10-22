"""Create samples/budget_actuals.xlsx (two sheets) from samples/budget_actuals.csv, for trying the Excel path."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    src = ROOT / "samples" / "budget_actuals.csv"
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget vs Actual"
    with src.open(newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0:
                ws.append(row)
                continue
            row[0] = datetime.strptime(row[0], "%Y-%m-%d")
            row[1] = int(row[1])
            row[5] = float(row[5]) if row[5] else None
            row[6] = float(row[6]) if row[6] else None
            ws.append(row)
    notes = wb.create_sheet("Read me")
    notes.append(["This workbook is synthetic demo data (fictional company)."])
    notes.append(["The first sheet has the column names in row 1 and one row per department, category and month."])
    out = ROOT / "samples" / "budget_actuals.xlsx"
    wb.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
