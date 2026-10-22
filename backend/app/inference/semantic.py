"""Semantic model: infer from the profile, then refine through chat."""

from __future__ import annotations

import logging

from ..engine.profile import TableProfile
from ..llm_client import LLMClient, LLMError
from ..spec.models import SemanticModel, SemanticRefinement
from . import prompts
from .heuristics import heuristic_semantic_model, hints_text
from .structured import call_structured

log = logging.getLogger("dashboard.inference")


def _reconcile(model: SemanticModel, profile: TableProfile, fallback: SemanticModel) -> SemanticModel:
    """Make sure the model covers exactly the profile's columns, in file order, with sane types."""
    by_name = {c.name: c for c in model.columns}
    lower = {c.name.lower(): c for c in model.columns}
    out = []
    for pc in profile.columns:
        sem = by_name.get(pc.name) or lower.get(pc.name.lower())
        fb = fallback.column(pc.name)
        if sem is None:
            sem = fb
        sem = sem.model_copy(update={"name": pc.name})
        if sem.data_type != pc.data_type and not (pc.data_type in ("integer", "number") and sem.data_type in ("integer", "number")):
            # The LLM may only move between compatible types; the engine's parsed type wins otherwise.
            if not (pc.data_type == "text" and sem.data_type in ("date", "datetime", "number", "integer", "boolean")):
                sem.data_type = pc.data_type  # type: ignore[assignment]
        if sem.role == "measure" and pc.data_type not in ("integer", "number"):
            sem.role = "attribute" if pc.data_type == "text" else "dimension"
        out.append(sem)
    model.columns = out
    if model.time_column and not profile.column(model.time_column):
        model.time_column = next((c.name for c in out if c.role == "time"), "")
    if not model.currency:
        model.currency = fallback.currency
    return model


def _validate(profile: TableProfile):
    names = {c.name for c in profile.columns}

    def check(model: SemanticModel) -> list[str]:
        errors = []
        unknown = [c.name for c in model.columns if c.name not in names]
        missing = [n for n in names if n not in {c.name for c in model.columns}]
        if unknown:
            errors.append(f"columns not in the table (use exact names): {unknown[:10]}")
        if missing:
            errors.append(f"columns missing from your answer: {missing[:10]}")
        return errors

    return check


def infer_semantic_model(profile: TableProfile, llm: LLMClient | None) -> tuple[SemanticModel, str]:
    """Returns (model, reply_for_the_user)."""
    fallback = heuristic_semantic_model(profile)
    if llm is None:
        return fallback, ("No AI model is configured, so the column meanings were guessed from the data. "
                          "Please review and correct them below.")
    try:
        res = call_structured(llm, system=prompts.SEMANTIC_SYSTEM,
                              user=prompts.semantic_user_prompt(profile, hints_text(profile)),
                              model_cls=SemanticModel, schema_name="semantic_model", validate_extra=_validate(profile))
    except LLMError as exc:
        log.warning("semantic inference failed, using heuristics: %s", exc)
        fallback.open_questions.insert(0, f"The AI step failed ({exc}); these are heuristic guesses.")
        return fallback, f"The AI could not analyse the file ({exc}). Showing heuristic guesses instead."
    model = _reconcile(res.obj, profile, fallback)  # type: ignore[arg-type]
    reply = (f"I think this is **{model.title}** - {model.description} "
             f"Data shape: {model.data_shape.replace('_', '-')} ({model.data_shape_reasoning}) "
             f"Confidence: {model.confidence}.")
    if model.open_questions:
        reply += " I have a few questions listed below - answer them in the chat or just edit the table."
    return model, reply


def refine_semantic_model(profile: TableProfile, model: SemanticModel, message: str, history: list[dict],
                          llm: LLMClient | None) -> tuple[SemanticModel, str]:
    if llm is None:
        note = message.strip()
        if note:
            model.business_notes.append(note)
        return model, ("No AI model is configured, so I saved your comment as a business note. "
                       "Edit the columns directly in the table.")
    res = call_structured(llm, system=prompts.SEMANTIC_REFINE_SYSTEM,
                          user=prompts.semantic_refine_prompt(profile, model, history, message),
                          model_cls=SemanticRefinement, schema_name="semantic_refinement",
                          validate_extra=lambda r: _validate(profile)(r.model))
    refined: SemanticRefinement = res.obj  # type: ignore[assignment]
    updated = _reconcile(refined.model, profile, heuristic_semantic_model(profile))
    if model.business_notes and not updated.business_notes:
        updated.business_notes = model.business_notes
    return updated, refined.reply or "Updated the model."
