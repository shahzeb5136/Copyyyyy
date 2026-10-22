# The dashboard file (spec) format

The dashboard file (`*.dashboard.json`) is the only thing the app persists for the user. It is produced and
validated by the Pydantic models in `backend/app/spec/models.py`; the JSON schema is available at
`GET /api/spec-schema`. Fields at their default value are omitted when saving.

```json
{
  "spec_version": "1.0",
  "metadata": { "title": "Budget vs actual", "description": "...", "created_at": "...", "app_version": "1.0.0",
                "llm_provider": "foundry", "llm_model": "claude-opus-5-5", "created_by": "jane@contoso.com" },
  "source_fingerprint": { "file_name": "budget_actuals.xlsx", "sheet": "Budget vs Actual", "row_count": 1584,
                          "columns": [ { "name": "period_month", "data_type": "date" }, ... ] },
  "semantic_model": { "title": "...", "domain": "finance", "grain": "one row per department, category and month",
                      "data_shape": "aggregated", "time_column": "period_month", "currency": "USD",
                      "columns": [ { "name": "budget_usd", "description": "...", "data_type": "number",
                                     "role": "measure", "semantic_type": "currency", "unit": "USD", "aggregation": "sum" } ],
                      "business_notes": [ "Only closed periods count as actuals" ] },
  "global_filters": [],
  "widgets": [ ... ]
}
```

## Widgets

```json
{ "id": "actual_by_category", "type": "chart", "title": "Where does the money go?",
  "subtitle": "sum of actual_usd per expense category, closed periods",
  "steps": [
    { "op": "filter", "conditions": [ { "column": "period_status", "operator": "eq", "value": "Closed" } ] },
    { "op": "aggregate", "group_by": ["expense_category"], "metrics": [ { "column": "actual_usd", "fn": "sum", "name": "actual" } ] },
    { "op": "sort", "columns": ["actual"], "descending": true },
    { "op": "top_n", "column": "actual", "n": 10, "value": "Other" }
  ],
  "chart": { "type": "bar", "x": "expense_category", "y": ["actual"], "orientation": "h", "value_format": "currency" },
  "layout": { "w": 6 } }
```

* `type`: `chart` (steps + chart) or `text` (markdown; `text.mode` = `static` with `content`, or `generated`
  with `source_widget_id` pointing at the chart to summarise; `stale` marks summaries written from older data).
* `layout.w`: width in a 12-column grid (3 = KPI tile, 6 = half, 12 = full width). Widgets are laid out in order.
* `chart.type`: `kpi`, `bar`, `line`, `area`, `scatter`, `pie`, `histogram`, `box`, `heatmap`, `treemap`,
  `waterfall`, `funnel`, `gantt`, `table`. Column names in `chart` refer to the **output** of the steps.
* `chart.value_format`: `number`, `currency` (uses `semantic_model.currency`), `percent`.

## Transformation steps

Steps run in order on the uploaded table (after `global_filters`). Each is one of a fixed set of
operations implemented in pandas (`backend/app/engine/transforms.py`). The model never produces code.

| op | fields | effect |
|---|---|---|
| `filter` | `conditions[]` (`column`, `operator`, `value`, `values[]`), `logic` and/or | Keep matching rows. Operators: `eq ne gt gte lt lte in not_in contains not_contains starts_with ends_with is_null not_null between`. Values are text; numbers/dates are parsed by the engine. For date columns `"2026"` / `"2026-03"` mean the whole year/month. |
| `derive` | `name`, `expression` | New column from arithmetic over columns, e.g. `actual - budget`, ``100 * `Net Sales (AED)` / `Cost` ``. Functions: abs round floor ceil sqrt log coalesce if min max year month quarter day weekday days_between len lower upper isnull. |
| `date_bucket` | `column`, `unit` (year/quarter/month/week/day), `name` | Period start for time series. |
| `date_part` | `column`, `unit` (year/quarter/month/month_name/week/weekday/day/hour), `name` | Calendar part. |
| `aggregate` | `group_by[]`, `metrics[]` (`column`, `fn`, `name`) | Group and aggregate: `sum mean median min max count count_distinct first last`; `column: ""` with `count` = row count; empty `group_by` = one total row. |
| `sort` | `columns[]`, `descending` | |
| `limit` | `n` | |
| `top_n` | `column`, `n`, `descending`, `value` | Keep the top n rows by a metric; `value` labels a row that folds the rest ("Other"). |
| `pivot` | `group_by[]`, `column`, `value`, `fn` | Wide table (values of `column` become columns). |
| `unpivot` | `group_by[]`, `columns[]`, `name`, `value` | Long table. |
| `rename` | `renames[]` (`from`, `to`) | |
| `select` | `columns[]` | |
| `cast` | `column`, `unit` (number/text/date/boolean) | |
| `drop_nulls` | `columns[]` | |
| `fill_nulls` | `column`, `value` | |
| `bin` | `column`, `n`, `name` | Equal-width buckets as text. |
| `pct_of_total` | `column`, `name`, `group_by[]` | Share in percent (within partition). |
| `cumulative` | `column`, `name`, `group_by[]` | Running total. |
| `period_change` | `column`, `name`, `n`, `kind` (abs/pct), `group_by[]` | Change vs n rows earlier (sort first). |
| `rank` | `column`, `name`, `descending`, `group_by[]` | |

## Compatibility on rebuild

On *Rebuild*, the app compares the new file with `source_fingerprint` and with the columns the widgets
actually use. Missing used columns and breaking type changes (e.g. a number column that is now text) are
reported per widget; unused missing columns, extra columns and large row-count changes are warnings. Charts
that can still be computed are shown; the rest show their error. The fingerprint in the spec is then
updated to the new file when you download it again.

## Versioning

`spec_version` is checked on load. Unknown versions are rejected; when the format changes, a migration is
registered in `backend/app/spec/migrations.py` so older files keep working.
