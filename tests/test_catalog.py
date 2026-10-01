"""Loading built-in datasets and handling uploaded CSVs."""

import pandas as pd

from core import catalog


def test_builtin_datasets_load(datasets):
    assert list(datasets) == ["finance", "hr", "projects"]
    for ds in datasets.values():
        assert ds.example_questions and ds.business_definitions
        for t in ds.tables:
            assert all(c.description for c in t.columns), f"{ds.id}.{t.name} has undocumented columns"


def test_schema_prompt_contains_definitions_and_profiles(datasets):
    text = catalog.schema_for_prompt(datasets["finance"])
    assert "TABLE budget_actuals" in text
    assert "values: 'Closed', 'Open'" in text
    assert "Positive variance means OVER budget" in text


def test_read_semicolon_csv_with_bom():
    content = "\ufeffName;Amount (€);Date\nA;1.234,5;2025-01-31\nB;10;2025-02-28\n".encode()  # noqa: UP012
    df = catalog.read_uploaded_csv(content)
    assert list(df.columns) == ["Name", "Amount (€)", "Date"]
    assert len(df) == 2


def test_latin1_fallback():
    content = "city,value\nZürich,1\nMálaga,2\n".encode("latin-1")
    df = catalog.read_uploaded_csv(content)
    assert df["city"].tolist() == ["Zürich", "Málaga"]


def test_type_inference_and_coercion():
    raw = pd.DataFrame({"d": ["31/01/2025", "15/02/2025"], "n": ["1,200", "3"], "f": ["1.5", "2.25"],
                        "b": ["Yes", "No"], "t": ["x", "y"]})
    types = {c: catalog.infer_column_type(raw[c]) for c in raw}
    assert types == {"d": "date", "n": "integer", "f": "decimal", "b": "boolean", "t": "text"}
    cols = [catalog.Column(c, t) for c, t in types.items()]
    df, failures = catalog.coerce_frame(raw, cols)
    assert df["d"].iloc[0] == pd.Timestamp("2025-01-31")
    assert df["n"].tolist() == [1200, 3]
    assert failures == {}


def test_coercion_reports_failures():
    s, failed = catalog.coerce_series(pd.Series(["1", "two", None]), "integer")
    assert failed == 1 and s.iloc[0] == 1


def test_identifier_sanitising():
    assert catalog.sanitize_identifier("Order Date") == "order_date"
    assert catalog.sanitize_identifier("Discount %") == "discount_pct"
    assert catalog.sanitize_identifier("customerID") == "customer_id"
    assert catalog.sanitize_identifier("2024 Sales") == "c_2024_sales"
    assert catalog.sanitize_identifier("select") == "select_value"
    assert catalog.dedupe(["a", "a", "b"]) == ["a", "a_2", "b"]
    assert catalog.is_valid_identifier("net_sales") and not catalog.is_valid_identifier("Net Sales")


def test_dictionary_yaml_round_trip(datasets):
    import yaml
    doc = yaml.safe_load(datasets["hr"].to_dictionary_yaml())
    assert doc["tables"][1]["name"] == "employees"
