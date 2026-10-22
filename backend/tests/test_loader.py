from __future__ import annotations

import pytest

from app.engine.loader import FileFormatError, coerce_series, infer_type, load_table
from tests.conftest import make_xlsx


def test_csv_types_and_shape(budget_table):
    t = budget_table
    assert t.types["period_month"] == "date"
    assert t.types["fiscal_year"] == "integer"
    assert t.types["actual_usd"] == "number"
    assert t.types["expense_category"] == "text"
    assert len(t.df) == 1584 and t.sheet == "" and t.sheet_names == []


def test_orders_boolean_and_currency_columns(orders_bytes):
    t = load_table(orders_bytes, "orders.csv")
    assert t.types["Returned?"] == "boolean"
    assert t.types["Unit Price (AED)"] == "number"
    assert t.types["Order Date"] == "date"


def test_excel_multi_sheet(xlsx_bytes):
    t = load_table(xlsx_bytes, "book.xlsx")
    assert t.sheet_names == ["Data", "Notes"] and t.sheet == "Data"
    assert t.types == {"Department": "text", "Month": "date", "Budget": "integer", "Actual": "integer",
                       "Closed?": "boolean"}
    assert len(t.df) == 18
    assert any("2 sheets" in w for w in t.warnings)
    t2 = load_table(xlsx_bytes, "book.xlsx", sheet="Notes")
    assert t2.sheet == "Notes" and list(t2.df.columns) == ["Notes"]


def test_excel_title_row_rejected():
    rows = [["Budget report 2026", None, None], ["Department", "Budget", "Actual"], ["IT", 1, 2], ["HR", 3, 4]]
    with pytest.raises(FileFormatError) as exc:
        load_table(make_xlsx({"Sheet1": rows}), "bad.xlsx")
    assert "first row" in str(exc.value).lower()


def test_excel_data_as_first_row_rejected():
    rows = [["IT", 1000, 950], ["HR", 1100, 1200], ["Ops", 900, 800]]
    with pytest.raises(FileFormatError, match="first row"):
        load_table(make_xlsx({"Sheet1": rows}), "bad.xlsx")


def test_csv_title_row_rejected():
    content = b"Budget report 2026\nDept,Amount,Year\nIT,5,2025\nHR,6,2025\nOps,7,2026\n"
    with pytest.raises(FileFormatError, match="column names"):
        load_table(content, "bad.csv")


def test_blank_header_columns_with_data_rejected():
    rows = [["Dept", "Budget", None, None], ["IT", 1, 2, 3], ["HR", 3, 4, 5]]
    with pytest.raises(FileFormatError, match="no name"):
        load_table(make_xlsx({"S": rows}), "bad.xlsx")


def test_duplicate_headers_renamed_and_empty_columns_dropped():
    content = b"Dept,Amount,Amount,\nIT,5,6,\nHR,7,8,\n"
    t = load_table(content, "dup.csv")
    assert list(t.df.columns) == ["Dept", "Amount", "Amount_2"]
    assert any("Duplicate" in w for w in t.warnings)


def test_unsupported_extension_and_xls():
    with pytest.raises(FileFormatError, match="Unsupported"):
        load_table(b"a,b\n1,2\n", "file.docx")
    with pytest.raises(FileFormatError, match=".xlsx"):
        load_table(b"", "file.xls")


def test_row_limit():
    content = b"a,b\n" + b"".join(f"{i},{i}\n".encode() for i in range(50))
    with pytest.raises(FileFormatError, match="more than 10"):
        load_table(content, "big.csv", max_rows=10)


def test_semicolon_and_cp1252():
    content = "Name;Amount;Date\nMüller;1.234,50;05/03/2026\nSchäfer;2.000,00;06/03/2026\n".encode("cp1252")
    t = load_table(content, "eu.csv")
    assert list(t.df.columns) == ["Name", "Amount", "Date"]
    assert t.df["Name"].iloc[0] == "Müller"


def test_dayfirst_dates():
    content = b"d,v\n25/01/2026,1\n26/01/2026,2\n27/01/2026,3\n"
    t = load_table(content, "d.csv")
    assert t.types["d"] == "date" and str(t.df["d"].iloc[0].date()) == "2026-01-25"


def test_mixed_numeric_column_kept_as_text():
    content = b"code,v\nA1,1\nB2,2\n3,3\n4,4\n5,5\n6,6\n"
    t = load_table(content, "m.csv")
    assert t.types["code"] == "text"


def test_infer_and_coerce_currency_strings():
    import pandas as pd

    s = pd.Series(["$1,200.50", "$300", "(45)", None])
    assert infer_type(s) == "number"
    out, failed = coerce_series(s, "number")
    assert out.tolist()[:3] == [1200.5, 300.0, -45.0] and failed == 0
