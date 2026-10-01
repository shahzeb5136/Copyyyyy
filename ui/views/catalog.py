"""🗂️ Data catalog - the tables and the semantic layer the AI works from."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.catalog import DUCKDB_TYPES, profile_column, to_display_frame

from .. import state
from ..styles import hero, label, md_escape

TYPE_ICONS = {"text": "🔤 text", "integer": "🔢 integer", "decimal": "🔢 decimal", "date": "📅 date",
              "datetime": "🕒 datetime", "boolean": "☑️ boolean"}


def render() -> None:
    ds = state.active_dataset()
    rows = sum(t.row_count for t in ds.tables)
    hero(
        "Data catalog",
        f"{ds.icon} {ds.name}",
        ds.description,
        [("Tables", str(len(ds.tables))), ("Rows", f"{rows:,}"),
         ("Source", "Uploaded by you" if ds.source == "uploaded" else "Built-in demo data")],
    )

    st.markdown(
        "This is everything the **SQL agent** sees before it writes a query: table and column names, "
        "data types, a short profile of each column and - most importantly - the plain-English **definitions**. "
        "Good definitions are what make text-to-SQL reliable."
    )

    tabs = st.tabs([f":material/table_chart: {t.name}" for t in ds.tables]
                   + [":material/menu_book: Business definitions", ":material/data_object: Prompt context"])
    for tab, table in zip(tabs, ds.tables):
        with tab:
            st.markdown(f"**{md_escape(table.description)}**")
            st.caption(f"{table.row_count:,} rows · {len(table.columns)} columns")
            dictionary = pd.DataFrame([
                {"Column": c.name, "Type": TYPE_ICONS.get(c.type, c.type), "Definition": c.description,
                 "Profile": profile_column(table.df[c.name], c.type)}
                for c in table.columns
            ])
            label("Column definitions")
            with st.container(key=f"dictionary-{table.name}"):
                st.table(dictionary, hide_index=True, border="horizontal")
            label("Preview")
            preview = table.df.head(200)
            st.dataframe(to_display_frame(preview), hide_index=True, width="stretch",
                         height=min(390, 38 + 35 * len(preview)))
            st.download_button(f"Download {table.name}.csv", table.df.to_csv(index=False).encode(),
                               file_name=f"{table.name}.csv", mime="text/csv", key=f"dl-{ds.id}-{table.name}",
                               icon=":material/download:")

    with tabs[len(ds.tables)]:
        if ds.business_definitions:
            st.markdown("Agreed metric logic the SQL agent must follow:")
            for d in ds.business_definitions:
                st.markdown(f"- {md_escape(d)}")
        else:
            st.caption("No business definitions for this dataset.")
        if ds.example_questions:
            label("Example questions")
            for q in ds.example_questions:
                st.markdown(f"- {md_escape(q)}")

    with tabs[len(ds.tables) + 1]:
        st.caption("The exact schema text sent to the SQL agent (plus the question and recent conversation).")
        st.code(state.schema_text_for(ds), language="text", wrap_lines=True)
        st.download_button("Download data dictionary (YAML)", ds.to_dictionary_yaml().encode(),
                           file_name=f"{ds.id}_dictionary.yaml", mime="text/yaml", key=f"dict-{ds.id}",
                           icon=":material/download:")
        st.caption("Engine types: " + ", ".join(f"{k} → {v}" for k, v in DUCKDB_TYPES.items()))
