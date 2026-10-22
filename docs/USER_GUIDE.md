# User guide

Dashboard Builder turns a spreadsheet into a dashboard in four steps, then lets you keep the dashboard as a
file and rebuild it with new data whenever you like.

## 1. Upload

**Create a new dashboard** - drop a `.csv` or `.xlsx` file (up to 50 MB by default). The column names
must be in the **first row**; if the sheet has a title row, notes or blank rows above the header you will get
a message asking you to put the column names in the first row and upload again. For Excel workbooks with
several sheets, the first sheet is used and you can switch to another on the next screen.

**Rebuild a saved dashboard** - drop the new data file *and* the `*.dashboard.json` file you downloaded
earlier, then click *Rebuild dashboard*. No AI is involved; the dashboard is recomputed from the new file.
If columns are missing or changed, a banner explains which charts are affected and the chat can fix the
dashboard ("the column Cost was renamed to Spend").

Sample files are offered on the home page if you want to try the app.

## 2. Check data

Shows what was read: number of rows and columns, the type found for each column (number, date, text, …),
the first rows, and any parsing notes (e.g. a column that could not be read as a number). Pick another sheet
here if needed, then *Analyse columns*.

## 3. Describe columns

The AI proposes the **semantic model**:

* a title, description, business domain and the "grain" (what one row represents);
* whether the table is **row-level records** (every row a transaction - charts must aggregate) or
  **already summarised**, with its reasoning;
* for every column: type, **role** (measure / dimension / time / identifier / attribute / ignore), unit or
  currency, default aggregation and a one-sentence description.

Correct anything directly in the table, or tell the AI in the chat ("amounts are in thousands", "only closed
periods count", "cost_center is the department"). Statements like these are kept as **business rules** and
are followed by every later step. The AI may list a few questions; answer them in the chat or ignore them.
Then *Build dashboard*.

What the AI sees at this point: column names, types, statistics (min/max/percentiles, blanks, distinct
counts), the most common values of text columns (up to 100 per column) and five sample rows - never the
whole file.

## 4. Dashboard

The AI designs a one-page dashboard: KPI tiles, a trend over time, breakdowns by the main categories,
comparisons such as budget vs actual, and an **executive summary** written from the data of the key chart.
Each widget has a menu (⋯): move up/down, wider/narrower, show the data behind the chart, download it as
CSV, rewrite a summary, remove.

**Change the dashboard** in the chat, for example:

* "Add a chart of net sales by country"
* "Add an executive summary under the monthly trend"
* "Remove the pie chart" / "Make the first chart full width"
* "Show the trend by quarter instead of month"
* "Add a note saying the Q4 figures are provisional"

The AI returns the updated dashboard and says what changed. Written summaries are regenerated when their
chart changes; after a rebuild with new data they are marked *written from previous data* until you click
*Refresh summaries*.

**Download dashboard file** saves `<title>.dashboard.json`. Keep it - it is the only copy. It contains the
column meanings, business rules, the definition of every chart and the layout, but **no data**, so it is
safe to e-mail. Next month, upload it together with the new file on the home page.

## Ask a question

The *Ask a question* tab answers ad-hoc questions about the uploaded data ("Which category is most over
budget this year?", "How did headcount change by quarter?"). The AI plans a calculation, the app runs it on
your file, and the AI writes a short answer with a chart or table. *Add to dashboard* pins the answer as a
new widget. Follow-up questions ("and by region?") understand the previous ones.

## Tips

* Clean headers help: one header row, one value per cell, no merged cells, no totals row at the bottom.
* Dates are recognised in ISO (`2026-03-01`), day-first (`01/03/2026`) and Excel date formats.
* Numbers with thousands separators, currency symbols or `%` are read as numbers.
* The more precise the column descriptions and business rules, the better the dashboard and the answers.
* Very large files (hundreds of thousands of rows) are fine; the app aggregates before charting. The upload
  limit is set by the administrator.
* Uploaded files are deleted from the server automatically after a retention period (72 hours by default).
