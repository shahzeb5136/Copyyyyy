"""Offline stand-in for an LLM - used by the tests and for UI development without an API key.

Enable it with ``LLM_PROVIDER=offline`` (or ``provider = "offline"`` in secrets).
It replays hand-written SQL and chart specs for the built-in example questions,
so every other part of the app (SQL sandbox, charts, UI) runs for real.
It is NOT an AI: unknown questions get a polite "not available offline" reply.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

from .llm import BaseLLM, JSONResult, Usage


def _key(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", q.lower()).strip()


def _bar(x, y, title, fmt="number", sort="desc", orientation="v", color="", barmode="group"):
    return {"chart_type": "bar", "title": title, "x": x, "y": y, "color": color, "size": "", "z": "", "path": [],
            "label": "", "start": "", "end": "", "orientation": orientation, "barmode": barmode, "sort": sort,
            "value_format": fmt}


def _spec(chart_type, title, **kw):
    base = {"chart_type": chart_type, "title": title, "x": "", "y": [], "color": "", "size": "", "z": "",
            "path": [], "label": "", "start": "", "end": "", "orientation": "v", "barmode": "group",
            "sort": "none", "value_format": "number"}
    base.update(kw)
    return base


CANNED: dict[str, tuple[str, list[dict]]] = {
    # ------------------------------------------------------------------ finance
    "What's the FY2025 budget vs actual spend by department?": (
        """SELECT d.department_name,
       ROUND(SUM(b.budget_usd)) AS budget_usd,
       ROUND(SUM(b.actual_usd)) AS actual_usd,
       ROUND(SUM(b.actual_usd) - SUM(b.budget_usd)) AS variance_usd,
       ROUND(100.0 * (SUM(b.actual_usd) - SUM(b.budget_usd)) / NULLIF(SUM(b.budget_usd), 0), 1) AS variance_pct
FROM budget_actuals b
JOIN departments d USING (department_id)
WHERE b.fiscal_year = 2025 AND b.period_status = 'Closed'
GROUP BY d.department_name
ORDER BY actual_usd DESC""",
        [_bar("department_name", ["budget_usd", "actual_usd"], "Spend tracked budget closely in FY2025",
              "currency", "desc", "h")],
    ),
    "Which expense categories are most over budget in 2026 YTD?": (
        """SELECT expense_category,
       ROUND(SUM(budget_usd)) AS budget_usd,
       ROUND(SUM(actual_usd)) AS actual_usd,
       ROUND(SUM(actual_usd) - SUM(budget_usd)) AS variance_usd,
       ROUND(100.0 * (SUM(actual_usd) - SUM(budget_usd)) / NULLIF(SUM(budget_usd), 0), 1) AS variance_pct
FROM budget_actuals
WHERE fiscal_year = 2026 AND period_status = 'Closed'
GROUP BY expense_category
ORDER BY variance_usd DESC""",
        [_bar("expense_category", ["variance_usd"], "Contractors and software lead the overspend",
              "currency", "desc", "h")],
    ),
    "Show Engineering's monthly actual spend vs budget for 2026.": (
        """SELECT b.period_month,
       ROUND(SUM(b.budget_usd)) AS budget_usd,
       ROUND(SUM(b.actual_usd)) AS actual_usd
FROM budget_actuals b
JOIN departments d USING (department_id)
WHERE d.department_name = 'Engineering' AND b.fiscal_year = 2026
GROUP BY b.period_month
ORDER BY b.period_month""",
        [_spec("line", "Engineering spend vs budget, 2026", x="period_month", y=["budget_usd", "actual_usd"],
               value_format="currency")],
    ),
    "How much of the FY2026 budget is left for each department?": (
        """SELECT d.department_name,
       ROUND(SUM(b.budget_usd)) AS fy2026_budget_usd,
       ROUND(SUM(b.actual_usd)) AS spent_ytd_usd,
       ROUND(SUM(b.budget_usd) - COALESCE(SUM(b.actual_usd), 0)) AS remaining_usd,
       ROUND(100.0 * SUM(b.actual_usd) / NULLIF(SUM(b.budget_usd), 0), 1) AS spent_pct
FROM budget_actuals b
JOIN departments d USING (department_id)
WHERE b.fiscal_year = 2026
GROUP BY d.department_name
ORDER BY remaining_usd DESC""",
        [_bar("department_name", ["spent_ytd_usd", "remaining_usd"], "FY2026 budget: spent vs remaining",
              "currency", "desc", "h", barmode="stack")],
    ),
    "What is revenue attainment vs target by region for 2026 YTD?": (
        """SELECT region,
       ROUND(SUM(target_usd)) AS target_usd,
       ROUND(SUM(actual_usd)) AS actual_usd,
       ROUND(100.0 * SUM(actual_usd) / NULLIF(SUM(target_usd), 0), 1) AS attainment_pct
FROM revenue
WHERE fiscal_year = 2026 AND period_status = 'Closed'
GROUP BY region
ORDER BY attainment_pct DESC""",
        [_bar("region", ["attainment_pct"], "Middle East & Africa leads on attainment", "percent")],
    ),
    "Break down 2025 revenue by region and product line.": (
        """SELECT region, product_line, ROUND(SUM(actual_usd)) AS revenue_usd
FROM revenue
WHERE fiscal_year = 2025
GROUP BY region, product_line
ORDER BY region, revenue_usd DESC""",
        [_spec("treemap", "2025 revenue mix", path=["region", "product_line"], y=["revenue_usd"],
               value_format="currency")],
    ),
    # ------------------------------------------------------------------ hr
    "What's our current headcount by department and location?": (
        """SELECT d.department_name, e.work_location, COUNT(*) AS headcount
FROM employees e
JOIN departments d USING (department_id)
WHERE e.employment_status = 'Active'
GROUP BY d.department_name, e.work_location
ORDER BY d.department_name, e.work_location""",
        [_spec("heatmap", "Headcount by department and office", x="work_location", y=["department_name"],
               z="headcount")],
    ),
    "What was voluntary attrition by department in 2025?": (
        """WITH hc AS (
    SELECT department_id,
           COUNT(*) FILTER (WHERE hire_date <= DATE '2025-01-01'
                            AND (termination_date IS NULL OR termination_date > DATE '2025-01-01')) AS hc_start,
           COUNT(*) FILTER (WHERE hire_date <= DATE '2025-12-31'
                            AND (termination_date IS NULL OR termination_date > DATE '2025-12-31')) AS hc_end,
           COUNT(*) FILTER (WHERE termination_type = 'Voluntary' AND year(termination_date) = 2025) AS voluntary_leavers
    FROM employees
    GROUP BY department_id
)
SELECT d.department_name,
       hc.voluntary_leavers,
       ROUND((hc.hc_start + hc.hc_end) / 2.0, 1) AS avg_headcount,
       ROUND(100.0 * hc.voluntary_leavers / NULLIF((hc.hc_start + hc.hc_end) / 2.0, 0), 1) AS voluntary_attrition_pct
FROM hc
JOIN departments d USING (department_id)
ORDER BY voluntary_attrition_pct DESC""",
        [_bar("department_name", ["voluntary_attrition_pct"], "Voluntary attrition by department, 2025",
              "percent", "desc", "h")],
    ),
    "Show average base salary by job level.": (
        """SELECT job_level,
       COUNT(*) AS employees,
       ROUND(AVG(base_salary_usd)) AS avg_base_salary_usd
FROM employees
WHERE employment_status = 'Active' AND employment_type <> 'Part-time'
GROUP BY job_level
ORDER BY CAST(substr(job_level, 2) AS INTEGER)""",
        [_bar("job_level", ["avg_base_salary_usd"], "Pay rises steeply from L5 upward", "currency", "none")],
    ),
    "How many people did we hire each quarter since 2024?": (
        """SELECT year(hire_date) || '-Q' || quarter(hire_date) AS hire_quarter,
       COUNT(*) AS new_hires
FROM employees
WHERE hire_date >= DATE '2024-01-01'
GROUP BY hire_quarter
ORDER BY hire_quarter""",
        [_bar("hire_quarter", ["new_hires"], "New hires per quarter", "number", "none")],
    ),
    "Which departments are below their approved 2026 headcount?": (
        """SELECT d.department_name,
       d.approved_headcount_2026 AS approved_headcount,
       COUNT(e.employee_id) AS current_headcount,
       d.approved_headcount_2026 - COUNT(e.employee_id) AS open_positions
FROM departments d
LEFT JOIN employees e
       ON e.department_id = d.department_id AND e.employment_status = 'Active'
GROUP BY d.department_name, d.approved_headcount_2026
HAVING d.approved_headcount_2026 - COUNT(e.employee_id) > 0
ORDER BY open_positions DESC""",
        [_bar("department_name", ["open_positions"], "Open positions vs 2026 plan", "number", "desc", "h")],
    ),
    "How many sick days were taken per department in 2026?": (
        """SELECT d.department_name,
       SUM(l.working_days) AS sick_days,
       COUNT(DISTINCT l.employee_id) AS employees_with_sick_leave
FROM leave_records l
JOIN employees e USING (employee_id)
JOIN departments d ON d.department_id = e.department_id
WHERE l.leave_type = 'Sick leave' AND year(l.start_date) = 2026
GROUP BY d.department_name
ORDER BY sick_days DESC""",
        [_bar("department_name", ["sick_days"], "Sick days by department, 2026 YTD", "number", "desc", "h")],
    ),
    # ------------------------------------------------------------------ projects
    "Show a timeline of all active projects.": (
        """SELECT project_name,
       start_date,
       COALESCE(forecast_end_date, planned_end_date) AS forecast_end_date,
       health,
       percent_complete
FROM projects
WHERE status IN ('In Progress', 'On Hold')
ORDER BY start_date""",
        [_spec("gantt", "Active project timeline", label="project_name", start="start_date",
               end="forecast_end_date", color="health")],
    ),
    "Which projects are over budget or forecast to slip, and by how much?": (
        """SELECT project_name,
       status,
       health,
       budget_usd,
       actual_cost_usd,
       ROUND(actual_cost_usd - budget_usd) AS budget_variance_usd,
       date_diff('day', planned_end_date, COALESCE(actual_end_date, forecast_end_date)) AS schedule_slip_days
FROM projects
WHERE actual_cost_usd > budget_usd
   OR COALESCE(actual_end_date, forecast_end_date) > planned_end_date
ORDER BY schedule_slip_days DESC""",
        [_bar("project_name", ["schedule_slip_days"], "Schedule slip by project (days)", "number", "desc", "h"),
         _bar("project_name", ["budget_variance_usd"], "Budget variance by project", "currency", "desc", "h")],
    ),
    "Show the task schedule for the Data Warehouse Modernization project.": (
        """SELECT t.task_name, t.phase, t.start_date, t.due_date, t.status, t.percent_complete
FROM tasks t
JOIN projects p USING (project_id)
WHERE p.project_name = 'Data Warehouse Modernization'
ORDER BY t.start_date, t.task_id""",
        [_spec("gantt", "Data Warehouse Modernization plan", label="task_name", start="start_date",
               end="due_date", color="status")],
    ),
    "How many overdue tasks does each project manager have?": (
        """SELECT m.full_name AS project_manager, COUNT(*) AS overdue_tasks
FROM tasks t
JOIN projects p USING (project_id)
JOIN team_members m ON m.member_id = p.project_manager_id
WHERE t.due_date < DATE '2026-09-30' AND t.status <> 'Completed'
GROUP BY m.full_name
ORDER BY overdue_tasks DESC""",
        [_bar("project_manager", ["overdue_tasks"], "Overdue tasks by project manager", "number", "desc", "h")],
    ),
    "What is budget utilisation by portfolio?": (
        """SELECT portfolio,
       SUM(budget_usd) AS budget_usd,
       ROUND(SUM(actual_cost_usd)) AS actual_cost_usd,
       ROUND(100.0 * SUM(actual_cost_usd) / NULLIF(SUM(budget_usd), 0), 1) AS utilisation_pct
FROM projects
GROUP BY portfolio
ORDER BY utilisation_pct DESC""",
        [_bar("portfolio", ["budget_usd", "actual_cost_usd"], "Budget vs cost to date by portfolio",
              "currency", "desc", "h")],
    ),
    "Which completed tasks took the most hours over estimate?": (
        """SELECT p.project_name || ' - ' || t.task_name AS task,
       t.estimated_hours,
       t.actual_hours,
       ROUND(t.actual_hours - t.estimated_hours, 1) AS hours_over_estimate,
       ROUND(100.0 * (t.actual_hours - t.estimated_hours) / NULLIF(t.estimated_hours, 0), 1) AS overrun_pct
FROM tasks t
JOIN projects p USING (project_id)
WHERE t.status = 'Completed' AND NOT t.is_milestone
ORDER BY hours_over_estimate DESC
LIMIT 10""",
        [_bar("task", ["hours_over_estimate"], "Biggest effort overruns", "number", "desc", "h")],
    ),
}
_CANNED = {_key(q): v for q, v in CANNED.items()}


def _question_from(prompt: str) -> str:
    m = re.search(r"<question>\s*(.*?)\s*</question>", prompt, flags=re.S)
    return m.group(1) if m else ""


class OfflineLLM(BaseLLM):
    provider = "offline"

    def __init__(self, api_key: str = "offline", model: str = "offline-replay", latency: float = 0.25):
        super().__init__(api_key or "offline", model)
        self.latency = latency

    def generate_json(self, *, system, user, schema, schema_name, max_tokens=8_000) -> JSONResult:
        time.sleep(self.latency)
        q = _key(_question_from(user))
        canned = _CANNED.get(q)
        if schema_name == "sql_query":
            if canned:
                data = {"reasoning": "Replayed a pre-written query (offline mode).", "can_answer": True,
                        "sql": canned[0], "assumptions": ""}
            else:
                data = {"reasoning": "", "can_answer": False, "sql": "",
                        "assumptions": "Offline mode can only replay the example questions. "
                                       "Configure an API key to ask anything."}
        elif schema_name == "chart_plan":
            data = {"reasoning": "Replayed a pre-written chart plan (offline mode).",
                    "charts": canned[1] if canned else []}
        elif schema_name == "data_dictionary":
            cols = re.findall(r"^\s+- (\S+) \(", user, flags=re.M)
            data = {"table_description": "Uploaded table (description drafted offline - please review).",
                    "columns": [{"name": c, "description": c.replace("_", " ").capitalize() + "."} for c in cols]}
        else:
            data = {}
        return JSONResult(data, Usage(), self.latency * 1000)

    def stream_text(self, *, system, user, max_tokens=4_000) -> Iterator[str]:
        rows = re.search(r"rows_returned: ([\d,]+)", user)
        text = (
            "**Offline demo mode** - the SQL and chart below were replayed, not generated by AI. "
            f"The query returned **{rows.group(1) if rows else 'some'} rows**; explore them in the tabs below. "
            "Add an API key to get a written analysis from the analyst agent."
        )
        for word in text.split(" "):
            time.sleep(0.01)
            yield word + " "
