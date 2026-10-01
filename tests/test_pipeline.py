"""The agent pipeline end-to-end, with scripted (fake) LLMs."""

import pytest

from core import charts, pipeline
from core.llm import BaseLLM, JSONResult, LLMError, Usage
from core.offline_llm import CANNED, OfflineLLM


class ScriptedLLM(BaseLLM):
    """Returns queued JSON responses and a fixed streamed answer; records prompts."""

    def __init__(self, responses):
        super().__init__("key", "scripted")
        self.responses = list(responses)
        self.prompts = []

    def generate_json(self, *, system, user, schema, schema_name, max_tokens=8_000):
        self.prompts.append(user)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return JSONResult(item, Usage(100, 20), 5.0)

    def stream_text(self, *, system, user, max_tokens=4_000):
        self.prompts.append(user)
        yield from ["The answer ", "is **42**."]


def _sql(sql, can_answer=True, assumptions=""):
    return {"reasoning": "r", "can_answer": can_answer, "sql": sql, "assumptions": assumptions}


@pytest.mark.parametrize("question", list(CANNED))
def test_every_example_question_runs(question, datasets, engines):
    ds = next(d for d in datasets.values() if question in d.example_questions)
    llm = OfflineLLM(latency=0)
    out = pipeline.run_sql_stage(question, ds, engines[ds.id], llm)
    assert out.ok, out.error
    assert out.result.row_count > 0
    viz = pipeline.design_charts(out, ds, llm)
    rendered, problems = charts.build_charts(viz.specs, out.result.df, ds.currency, ds.as_of_date)
    assert rendered and not problems, problems


def test_self_repair_after_sql_error(datasets, engines):
    ds = datasets["finance"]
    llm = ScriptedLLM([_sql("SELECT nonexistent FROM budget_actuals"),
                       _sql("SELECT COUNT(*) AS n FROM budget_actuals")])
    out = pipeline.run_sql_stage("how many rows?", ds, engines["finance"], llm)
    assert out.ok
    assert len(out.failed_attempts) == 1
    assert "nonexistent" in llm.prompts[1]  # the error was fed back to the model
    assert [t.agent for t in out.trace] == ["SQL agent", "Database", "SQL agent", "Database"]


def test_gives_up_after_max_repairs(datasets, engines):
    llm = ScriptedLLM([_sql("SELECT bad FROM x")] * 3)
    out = pipeline.run_sql_stage("q", datasets["finance"], engines["finance"], llm, max_repairs=2)
    assert not out.ok and out.error and len(out.failed_attempts) == 3


def test_dangerous_sql_is_never_executed(datasets, engines):
    llm = ScriptedLLM([_sql("DROP TABLE budget_actuals")] * 3)
    out = pipeline.run_sql_stage("drop it", datasets["finance"], engines["finance"], llm)
    assert not out.ok
    assert engines["finance"].execute("SELECT COUNT(*) AS n FROM budget_actuals").df.iloc[0]["n"] == 1584


def test_cannot_answer(datasets, engines):
    llm = ScriptedLLM([_sql("", can_answer=False, assumptions="No weather data here.")])
    out = pipeline.run_sql_stage("weather?", datasets["finance"], engines["finance"], llm)
    assert not out.can_answer and out.assumptions == "No weather data here."


def test_llm_error_is_reported(datasets, engines):
    llm = ScriptedLLM([LLMError("rate limited")])
    out = pipeline.run_sql_stage("q", datasets["finance"], engines["finance"], llm)
    assert out.error == "rate limited"


def test_schema_and_history_reach_the_prompt(datasets, engines):
    ds = datasets["projects"]
    llm = ScriptedLLM([_sql("SELECT 1 AS x")])
    history = [{"question": "Show active projects", "sql": "SELECT * FROM projects", "summary": "10 rows"}]
    pipeline.run_sql_stage("and their managers?", ds, engines["projects"], llm, history=history)
    prompt = llm.prompts[0]
    assert "TABLE tasks" in prompt and "Overdue task" in prompt and "Show active projects" in prompt


def test_answer_stream_and_history_entry(datasets, engines):
    ds = datasets["finance"]
    llm = ScriptedLLM([_sql("SELECT department_name FROM departments")])
    out = pipeline.run_sql_stage("list departments", ds, engines["finance"], llm)
    text = "".join(pipeline.stream_answer(out, ds, llm))
    assert text == "The answer is **42**."
    assert "rows_returned: 8" in llm.prompts[-1]
    entry = pipeline.history_entry(out, text)
    assert entry["sql"].startswith("SELECT") and "8 rows" in entry["summary"]


def test_viz_agent_failure_is_contained(datasets, engines):
    ds = datasets["finance"]
    llm = ScriptedLLM([_sql("SELECT department_name FROM departments"), LLMError("boom")])
    out = pipeline.run_sql_stage("list", ds, engines["finance"], llm)
    viz = pipeline.design_charts(out, ds, llm)
    assert viz.specs == [] and viz.trace.status == "error"


def test_draft_dictionary(datasets):
    import pandas as pd
    df = pd.DataFrame({"order_id": ["A1"], "net_sales_aed": [10.5]})
    llm = ScriptedLLM([{"table_description": "Orders.", "columns": [
        {"name": "order_id", "description": "Order key."}, {"name": "net_sales_aed", "description": "Sales."}]}])
    out = pipeline.draft_dictionary("orders", df, {"order_id": "text", "net_sales_aed": "decimal"}, llm)
    assert out["columns"]["order_id"] == "Order key." and out["table_description"] == "Orders."
