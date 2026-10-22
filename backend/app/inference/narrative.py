"""Executive summaries for text widgets with mode=generated."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd

from ..engine.dashboard import widget_data_for_llm
from ..llm_client import LLMClient, LLMError
from ..spec.models import DashboardSpec
from . import prompts

log = logging.getLogger("dashboard.inference")


def generate_narratives(df: pd.DataFrame, spec: DashboardSpec, llm: LLMClient | None, only_ids: list[str] | None = None,
                        force: bool = False, progress=None) -> tuple[int, list[str]]:
    """Fill generated text widgets in place. Returns (count_written, warnings)."""
    warnings: list[str] = []
    if llm is None:
        return 0, ["No AI model is configured: summaries cannot be generated."]
    written = 0
    targets = [w for w in spec.widgets if w.type == "text" and w.text.mode == "generated"
               and (only_ids is None or w.id in only_ids) and (force or not w.text.content or w.text.stale)]
    for i, w in enumerate(targets, 1):
        src = spec.widget(w.text.source_widget_id) if w.text.source_widget_id else None
        if src is None or src.type != "chart":
            warnings.append(f"'{w.title or w.id}': its source chart '{w.text.source_widget_id}' no longer exists.")
            continue
        if progress:
            progress(f"Writing summary {i} of {len(targets)}: {src.title}…")
        data_csv = widget_data_for_llm(df, src, spec)
        try:
            resp = llm.complete_text(system=prompts.NARRATIVE_SYSTEM,
                                     user=prompts.narrative_prompt(src.title, src.subtitle, src.chart.type,
                                                                   spec.semantic_model, data_csv), max_tokens=1_200)
        except LLMError as exc:
            warnings.append(f"'{w.title or w.id}': {exc}")
            continue
        w.text.content = resp.text.strip()
        w.text.generated_at = datetime.now(UTC).isoformat(timespec="seconds")
        w.text.stale = False
        written += 1
    return written, warnings
