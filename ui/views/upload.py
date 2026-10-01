"""⬆️ Bring your own data - upload CSVs and define what the columns mean."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pandas as pd
import streamlit as st

from core import catalog, pipeline
from core.catalog import COLUMN_TYPES, Column, Dataset, Table
from core.llm import LLMError

from .. import registry, state
from ..styles import hero, label, md_escape

log = logging.getLogger("askdata")
SAMPLE_FILE = catalog.DATA_DIR / "samples" / "online_store_orders.csv"
MAX_FILES = 5


def _drafts() -> dict[str, dict]:
    return st.session_state.setdefault("upload_drafts", {})


def _new_draft(file_name: str, content: bytes, from_sample: bool = False) -> dict:
    cfg = state.settings()
    draft = {"file_name": file_name, "from_sample": from_sample, "version": 0, "error": ""}
    try:
        raw = catalog.read_uploaded_csv(content, max_rows=cfg.max_upload_rows)
    except Exception as exc:  # noqa: BLE001
        draft["error"] = str(exc)
        return draft
    original = list(raw.columns)
    names = catalog.dedupe([catalog.sanitize_identifier(c) for c in original])
    rows = []
    for orig, name in zip(original, names):
        samples = raw[orig].dropna().astype(str).str.strip()
        samples = samples[samples != ""].drop_duplicates().head(3).tolist()
        rows.append({"original": orig, "name": name, "type": catalog.infer_column_type(raw[orig]),
                     "description": "", "sample": ", ".join(s[:30] for s in samples)})
    draft.update(
        raw=raw,
        table_name=catalog.sanitize_identifier(Path(file_name).stem, "table"),
        table_description="",
        columns=pd.DataFrame(rows),
    )
    return draft


def _file_key(name: str, content: bytes) -> str:
    return hashlib.sha1(name.encode() + content[:65_536] + str(len(content)).encode()).hexdigest()[:12]


def render() -> None:
    cfg = state.settings()
    hero(
        "Bring your own data",
        "Ask questions about your own CSV files",
        "Each file becomes a table. Tell the AI what every column means - those definitions are the "
        "'semantic layer' it uses to write correct SQL.",
    )
    st.info(
        "Uploaded files live only in your browser session: they are not stored on the server and disappear "
        "when you close the tab. Column names, a few sample values and query results are sent to the "
        "configured AI provider - please don't upload confidential data.",
        icon="🔒",
    )

    _existing_uploads()

    # ---------------------------------------------------------------- step 1
    label("Step 1 · Upload CSV files")
    files = st.file_uploader("CSV files (one table per file)", type=["csv", "tsv", "txt"],
                             accept_multiple_files=True, label_visibility="collapsed")
    c1, c2 = st.columns([3, 2], vertical_alignment="center")
    c1.caption(f"Up to {MAX_FILES} files · max {cfg.max_upload_rows:,} rows each · comma, semicolon, "
               "tab or pipe delimited · first row must be the header.")
    if SAMPLE_FILE.exists() and c2.button("No file handy? Load a sample CSV", icon=":material/science:",
                                          width="stretch"):
        _drafts()["sample"] = _new_draft(SAMPLE_FILE.name, SAMPLE_FILE.read_bytes(), from_sample=True)

    drafts = _drafts()
    current_keys = set()
    for f in (files or [])[:MAX_FILES]:
        content = f.getvalue()
        key = _file_key(f.name, content)
        current_keys.add(key)
        if key not in drafts:
            drafts[key] = _new_draft(f.name, content)
    if files and len(files) > MAX_FILES:
        st.warning(f"Only the first {MAX_FILES} files are used.")
    for key in [k for k, d in drafts.items() if not d["from_sample"] and k not in current_keys]:
        drafts.pop(key)  # file was removed from the uploader

    if not drafts:
        st.caption("Upload a file (or load the sample) to continue.")
        return

    # ---------------------------------------------------------------- step 2
    label("Step 2 · Describe each table and its columns")
    llm, llm_problem = state.get_llm()
    for key, draft in list(drafts.items()):
        _draft_editor(key, draft, llm, llm_problem)

    # ---------------------------------------------------------------- step 3
    valid_drafts = {k: d for k, d in drafts.items() if not d.get("error")}
    if not valid_drafts:
        return
    label("Step 3 · Create the dataset")
    with st.container(border=True):
        default_name = "Sample · Online store" if all(d["from_sample"] for d in valid_drafts.values()) else "My data"
        c1, c2 = st.columns([1, 2])
        name = c1.text_input("Dataset name", value=default_name, max_chars=60)
        description = c2.text_input("What is this data about? (optional)",
                                    placeholder="e.g. Online store orders for 2025, one row per order line")
        if st.button("Create dataset and start asking", type="primary", icon=":material/rocket_launch:"):
            _create_dataset(name, description, valid_drafts)


def _draft_editor(key: str, draft: dict, llm, llm_problem: str) -> None:
    with st.container(border=True):
        head, remove = st.columns([6, 1], vertical_alignment="center")
        if draft.get("error"):
            head.markdown(f"**{md_escape(draft['file_name'])}**")
            st.error(f"Couldn't read this file: {draft['error']}")
            if remove.button("Remove", key=f"rm-{key}", width="stretch"):
                _drafts().pop(key, None)
                st.rerun()
            return
        raw: pd.DataFrame = draft["raw"]
        head.markdown(f"**{md_escape(draft['file_name'])}** · {len(raw):,} rows × {raw.shape[1]} columns")
        if remove.button("Remove", key=f"rm-{key}", width="stretch", icon=":material/close:"):
            _drafts().pop(key, None)
            st.rerun()

        c1, c2 = st.columns([1, 2])
        draft["table_name"] = c1.text_input("Table name (used in SQL)", value=draft["table_name"],
                                            key=f"tname-{key}-{draft['version']}", max_chars=63)
        draft["table_description"] = c2.text_input(
            "Table description", value=draft["table_description"], key=f"tdesc-{key}-{draft['version']}",
            placeholder="What does one row represent? e.g. One row per order line")

        edited = st.data_editor(
            draft["columns"],
            key=f"editor-{key}-{draft['version']}",
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_order=["original", "name", "type", "description", "sample"],
            column_config={
                "original": st.column_config.TextColumn("CSV header", disabled=True, width="small"),
                "name": st.column_config.TextColumn("Column name (SQL)", required=True, width="medium",
                                                    help="snake_case: lowercase letters, digits and underscores"),
                "type": st.column_config.SelectboxColumn("Type", options=COLUMN_TYPES, required=True,
                                                         width="small"),
                "description": st.column_config.TextColumn(
                    "Definition (what does this column mean?)", width="large",
                    help="Business meaning, units/currency, allowed values, how it relates to other columns."),
                "sample": st.column_config.TextColumn("Sample values", disabled=True, width="medium"),
            },
        )
        draft["live_columns"] = edited
        missing = int((edited["description"].fillna("").str.strip() == "").sum())

        b1, b2 = st.columns([2, 3], vertical_alignment="center")
        draft_clicked = b1.button("Draft definitions with AI", key=f"ai-{key}", icon=":material/auto_awesome:",
                                  disabled=llm is None, help=llm_problem or "Fills in empty definitions for you to review.")
        b2.caption(f"{missing} column definition(s) still empty." if missing else "All columns have definitions ✓")
        with st.expander("Preview rows"):
            st.dataframe(raw.head(20), hide_index=True, width="stretch")

        if draft_clicked and llm is not None:
            with st.spinner("Drafting definitions…"):
                try:
                    preview = raw.copy()
                    preview.columns = edited["name"].tolist()
                    types = dict(zip(edited["name"], edited["type"]))
                    result = pipeline.draft_dictionary(draft["table_name"], preview.head(500), types, llm)
                except LLMError as exc:
                    st.error(str(exc))
                    return
                except Exception as exc:  # noqa: BLE001
                    log.exception("Dictionary drafting failed")
                    st.error(f"Couldn't draft definitions: {exc}")
                    return
            updated = edited.copy()
            for i, row in updated.iterrows():
                if not str(row["description"] or "").strip():
                    updated.at[i, "description"] = result["columns"].get(row["name"], "")
            draft["columns"] = updated
            if not draft["table_description"].strip():
                draft["table_description"] = result["table_description"]
            draft["version"] += 1
            st.rerun()


def _create_dataset(name: str, description: str, drafts: dict[str, dict]) -> None:
    errors: list[str] = []
    tables: list[Table] = []
    warnings: list[str] = []
    table_names: set[str] = set()
    for draft in drafts.values():
        cols_df: pd.DataFrame = draft.get("live_columns", draft["columns"])
        tname = (draft["table_name"] or "").strip()
        fname = draft["file_name"]
        if not catalog.is_valid_identifier(tname):
            errors.append(f"**{fname}**: table name `{tname}` must be snake_case (letters, digits, underscores) "
                          "and not a reserved SQL word.")
        if tname in table_names:
            errors.append(f"**{fname}**: table name `{tname}` is used twice.")
        table_names.add(tname)
        names = [str(n or "").strip() for n in cols_df["name"]]
        bad = [n for n in names if not catalog.is_valid_identifier(n)]
        if bad:
            errors.append(f"**{fname}**: invalid column name(s): " + ", ".join(f"`{b}`" for b in bad[:6]))
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            errors.append(f"**{fname}**: duplicate column name(s): " + ", ".join(f"`{d}`" for d in dupes))
        empty = [n for n, d in zip(names, cols_df["description"]) if not str(d or "").strip()]
        if empty:
            errors.append(f"**{fname}**: add a definition for {len(empty)} column(s): "
                          + ", ".join(f"`{e}`" for e in empty[:8]) + ("…" if len(empty) > 8 else ""))
        if errors:
            continue
        columns = [Column(n, t, " ".join(str(d).split())) for n, t, d in
                   zip(names, cols_df["type"], cols_df["description"])]
        raw = draft["raw"].copy()
        raw.columns = names
        df, failures = catalog.coerce_frame(raw, columns)
        for col, n in failures.items():
            warnings.append(f"`{tname}.{col}`: {n:,} value(s) couldn't be read as "
                            f"{next(c.type for c in columns if c.name == col)} and were set to empty (NULL).")
        tables.append(Table(tname, (draft["table_description"] or f"Uploaded from {fname}").strip(), columns, df))

    if errors:
        st.error("Please fix the following before creating the dataset:\n\n" + "\n".join(f"- {e}" for e in errors))
        return

    base_id = catalog.make_dataset_id(name or "my_data")
    ds_id, n = base_id, 2
    existing = state.all_datasets()
    while ds_id in existing:
        ds_id, n = f"{base_id}_{n}", n + 1
    ds = Dataset(id=ds_id, name=(name or "My data").strip(), icon="📁",
                 description=description.strip() or "Data uploaded by the user in this session.",
                 tables=tables, as_of_date="", currency="", source="uploaded",
                 example_questions=_starter_questions(tables))
    try:
        state.add_uploaded_dataset(ds)
        state.engine_for(ds)  # builds the SQL tables now so problems surface here
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to load uploaded dataset")
        state.remove_uploaded_dataset(ds.id)
        st.error(f"Couldn't load the data into the SQL engine: {exc}")
        return
    state.set_active_dataset(ds.id)
    st.session_state["upload_drafts"] = {}
    st.session_state["upload_success"] = (ds.name, warnings)
    if "ask" in registry.PAGES:
        st.switch_page(registry.PAGES["ask"])
    st.rerun()


def _starter_questions(tables: list[Table]) -> list[str]:
    t = tables[0]
    num = [c for c in t.columns if c.type in ("integer", "decimal")]
    cat = [c for c in t.columns if c.type == "text" and 1 < t.df[c.name].nunique() <= 50]
    dates = [c for c in t.columns if c.type in ("date", "datetime")]
    qs = [f"How many rows are in {t.name}, and what does a typical row look like?"]
    if num and cat:
        qs.append(f"What is the total {num[0].name.replace('_', ' ')} by {cat[0].name.replace('_', ' ')}?")
    if num and dates:
        qs.append(f"Show the monthly trend of {num[0].name.replace('_', ' ')}.")
    if len(cat) > 1:
        qs.append(f"Which {cat[1].name.replace('_', ' ')} values are most common?")
    return qs[:4]


def _existing_uploads() -> None:
    uploads = state.uploaded_datasets()
    success = st.session_state.pop("upload_success", None)
    if success:
        name, warnings = success
        st.success(f"Dataset **{md_escape(name)}** is ready.", icon="✅")
        for w in warnings:
            st.warning(w)
    if not uploads:
        return
    label("Your uploaded datasets (this session)")
    for ds in list(uploads.values()):
        with st.container(border=True):
            c1, c2, c3 = st.columns([5, 1.4, 1.2], vertical_alignment="center")
            c1.markdown(f"**{ds.icon} {md_escape(ds.name)}** · "
                        + ", ".join(f"`{t.name}` ({t.row_count:,} rows)" for t in ds.tables))
            if c2.button("Ask questions", key=f"use-{ds.id}", width="stretch", type="primary"):
                state.set_active_dataset(ds.id)
                if "ask" in registry.PAGES:
                    st.switch_page(registry.PAGES["ask"])
            if c3.button("Delete", key=f"del-{ds.id}", width="stretch", icon=":material/delete:"):
                state.remove_uploaded_dataset(ds.id)
                st.rerun()
