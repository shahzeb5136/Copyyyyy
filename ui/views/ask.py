"""💬 Ask - the main chat page."""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from core import pipeline
from core.catalog import Dataset
from core.config import ANTHROPIC_MODELS, OPENAI_MODELS
from core.llm import BaseLLM, LLMError
from core.pipeline import TraceStep, VizOutcome

from .. import components, state
from ..styles import PIPELINE_STEPS, hero, label, md_escape, steps

log = logging.getLogger("askdata")

USER_AVATAR = ":material/person:"
ASSISTANT_AVATAR = ":material/insights:"


def _queue_question(question: str) -> None:
    st.session_state["pending_question"] = question


def _model_label() -> str:
    provider, model = state.current_provider(), state.current_model()
    if provider == "offline":
        return "Offline replay (no AI)"
    names = ANTHROPIC_MODELS if provider == "anthropic" else OPENAI_MODELS
    return names.get(model, model).split(" (")[0]


def render() -> None:
    cfg = state.settings()
    ds = state.active_dataset()
    messages = state.chat_for(ds.id)
    llm, llm_problem = state.get_llm()
    left = state.questions_left()
    limit_reached = left == 0

    prompt = st.chat_input(
        f"Ask a question about {ds.name.split('·')[-1].strip()}…",
        disabled=llm is None or limit_reached,
        max_chars=600,
        key="chat_input",
    )
    question = prompt or st.session_state.pop("pending_question", None)

    tables = ", ".join(t.name for t in ds.tables)
    chips = [("Dataset", ds.name), ("Tables", tables)]
    if ds.as_of_date:
        chips.append(("Data as of", ds.as_of_date))
    hero("Talk to your data", cfg.app_title, cfg.app_tagline, chips)

    success = st.session_state.pop("upload_success", None)
    if success:
        name, warnings = success
        st.success(f"Dataset **{md_escape(name)}** is ready - ask your first question below.", icon="✅")
        for w in warnings:
            st.warning(w)
    if llm is None:
        st.info(llm_problem, icon="🔑")
    if limit_reached:
        st.warning(
            f"You've used all {cfg.max_questions_per_session} questions for this session. "
            "Thanks for trying the demo! Reload the page later to start a new session.",
            icon="⏳",
        )

    if not messages and not question:
        steps(PIPELINE_STEPS)
        label(f"Try a question about {ds.name}")
        with st.container(key="examples"):
            cols = st.columns(2)
            for i, q in enumerate(ds.example_questions or ["What does this dataset contain?"]):
                cols[i % 2].button(q, key=f"example-{ds.id}-{i}", width="stretch", on_click=_queue_question,
                                   args=(q,), disabled=llm is None or limit_reached, icon=":material/arrow_outward:")
        st.caption("Or type your own question below. Follow-ups like “now split that by quarter” work too.")
    elif messages:
        _, right = st.columns([5, 1])
        if right.button("Clear chat", icon=":material/delete_sweep:", width="stretch", key=f"clear-{ds.id}"):
            state.clear_chat(ds.id)
            st.rerun()

    for msg in messages:
        if msg["role"] == "user":
            with st.chat_message("user", avatar=USER_AVATAR):
                st.markdown(md_escape(msg["content"]))
        else:
            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                components.render_assistant_message(msg, ds)

    if question:
        if llm is None or limit_reached:
            st.toast("Can't ask right now - see the message above.", icon="⚠️")
            return
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(md_escape(question))
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            msg = answer_question(question, ds, llm)
        messages.append({"role": "user", "content": question})
        messages.append(msg)
        if msg.get("kind") != "error":  # failed calls (bad key, outage) don't use up the quota
            state.increment_questions()


def answer_question(question: str, ds: Dataset, llm: BaseLLM) -> dict:
    """Run the full agent pipeline, rendering progress live. Returns the message to store."""
    t0 = time.perf_counter()
    msg: dict = {"role": "assistant", "id": uuid.uuid4().hex[:10], "model": _model_label(), "kind": "answer"}
    status = st.status("Understanding your question…", expanded=True)

    def on_event(text: str) -> None:
        status.update(label=text)
        status.write(text)

    try:
        outcome = pipeline.run_sql_stage(
            question, ds, state.engine_for(ds), llm,
            history=state.history_for(ds.id)[-4:], on_event=on_event, schema_text=state.schema_text_for(ds),
        )
    except Exception as exc:  # noqa: BLE001 - never crash the page
        log.exception("SQL stage failed")
        status.update(label="Something went wrong", state="error", expanded=False)
        msg.update(kind="error", answer=f"Unexpected error while preparing the query: {exc}")
        components.render_assistant_message(msg, ds)
        return msg

    msg.update(sql=outcome.sql, reasoning=outcome.reasoning, assumptions=outcome.assumptions,
               failed_attempts=outcome.failed_attempts, trace=list(outcome.trace))

    if outcome.error:
        status.update(label="Couldn't answer this one", state="error", expanded=False)
        msg.update(kind="error", answer=outcome.error, elapsed=time.perf_counter() - t0)
        components.render_assistant_message(msg, ds)
        return msg

    if not outcome.can_answer:
        status.update(label="That's outside what this dataset covers", state="complete", expanded=False)
        msg.update(kind="cannot_answer",
                   answer=outcome.assumptions or "I couldn't find data in this dataset to answer that question.")
        components.render_assistant_message(msg, ds)
        state.history_for(ds.id).append(pipeline.history_entry(outcome))
        return msg

    result = outcome.result
    status.write(f"✅ Query returned {result.row_count:,} row(s) in {result.elapsed_ms:.0f} ms")
    on_event("✍️ Analyst agent is writing the answer · 🎨 Visualization agent is designing a chart…")

    # The visualization agent works in parallel while the answer streams in.
    viz_llm, _ = state.get_llm()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(pipeline.design_charts, outcome, ds, viz_llm or llm)

    answer_box = st.empty()
    text = ""
    t_answer = time.perf_counter()
    try:
        for chunk in pipeline.stream_answer(outcome, ds, llm):
            text += chunk
            answer_box.markdown(md_escape(text) + " ▌")
        answer_box.markdown(md_escape(text))
        msg["trace"].append(TraceStep("Analyst agent", "Write the answer (streamed)",
                                      (time.perf_counter() - t_answer) * 1000, usage=llm.last_stream_usage))
    except LLMError as exc:
        text = f"_The analyst agent couldn't write a summary ({exc}). The query results are below._"
        answer_box.markdown(md_escape(text))
        msg["trace"].append(TraceStep("Analyst agent", "Write the answer", 0, "error", str(exc)))
    except Exception as exc:  # noqa: BLE001
        log.exception("Answer streaming failed")
        text = "_The analyst agent hit an unexpected error. The query results are below._"
        answer_box.markdown(text)
        msg["trace"].append(TraceStep("Analyst agent", "Write the answer", 0, "error", str(exc)))

    try:
        viz: VizOutcome = future.result(timeout=150)
    except Exception as exc:  # noqa: BLE001
        log.exception("Visualization agent failed")
        viz = VizOutcome([], "", TraceStep("Visualization agent", "Design charts", 0, "error", str(exc)))
    finally:
        executor.shutdown(wait=False)

    elapsed = time.perf_counter() - t0
    msg.update(answer=text, df=result.df, truncated=result.truncated, charts=[s.to_dict() for s in viz.specs],
               chart_reasoning=viz.reasoning, elapsed=elapsed)
    msg["trace"].append(viz.trace)
    status.update(label=f"Done in {elapsed:.1f}s - SQL agent → database → analyst + visualization agents",
                  state="complete", expanded=False)

    components.render_assistant_message(msg, ds, show_answer=False)
    state.history_for(ds.id).append(pipeline.history_entry(outcome, text))
    return msg
