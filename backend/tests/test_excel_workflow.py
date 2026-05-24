from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from backend.app.excel_workflow import (
    build_excel_workflow_answer,
    build_excel_workflow_html_app,
    is_excel_workflow_request,
)


def test_copy_paste_automation_request_routes_to_spreadsheet_workflow_lane():
    assert is_excel_workflow_request(
        "I copy/paste rows between these spreadsheets every week; automate the manual edits into a local HTML app."
    )


def test_generic_automation_words_do_not_route_to_spreadsheet_workflow_lane():
    assert not is_excel_workflow_request("Can you automate the summary of these files?")
    assert not is_excel_workflow_request("Build a local app for my notes")


def test_reconcile_uses_actual_preview_source_rows_for_gapped_xlsx_summary():
    gapped_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.xlsx\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: Forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "Preview (source rows 3, 5):\n"
        "| SKU | Qty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
        "| B2 | 20 |\n"
    )
    raw_csv_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "Preview (source rows 2-3):\n"
        "| SKU | Qty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
        "| B2 | 25 |\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "B2,25\n"
        "```\n"
    )

    result = build_excel_workflow_answer(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.xlsx", "text": gapped_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": raw_csv_summary},
        ],
        [],
    )

    assert result is not None
    assert "B2` differs" in result["answer"]
    assert "20 at forecast.xlsx / Forecast row 5" in result["answer"]
    assert "20 at forecast.xlsx / Forecast row 3" not in result["answer"]


def test_reconcile_uses_raw_csv_physical_source_rows_with_blank_lines():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.xlsx\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: Forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "Preview:\n"
        "| SKU | Qty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
        "| B2 | 20 |\n"
    )
    raw_csv_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "Preview (source rows 2, 4):\n"
        "| SKU | Qty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
        "| B2 | 25 |\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "\n"
        "B2,25\n"
        "```\n"
    )

    result = build_excel_workflow_answer(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.xlsx", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": raw_csv_summary},
        ],
        [],
    )

    assert result is not None
    assert "B2` differs" in result["answer"]
    assert "25 at actuals.csv / actuals row 4" in result["answer"]
    assert "25 at actuals.csv / actuals row 3" not in result["answer"]


def test_reconcile_reports_each_parsed_table_row_count_in_scope():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.xlsx\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: Forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "Preview (source rows 2-3):\n"
        "| SKU | Qty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
        "| B2 | 20 |\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 3\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "B2,20\n"
        "C3,30\n"
        "```\n"
    )

    result = build_excel_workflow_answer(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.xlsx", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert result is not None
    assert "Parsed table scope: forecast.xlsx / Forecast (2 rows); actuals.csv / actuals (3 rows)." in result["answer"]
    assert result["evidence"]["table_rows"] == {
        "forecast.xlsx / Forecast": 2,
        "actuals.csv / actuals": 3,
    }


def test_schema_only_reconcile_evidence_includes_table_row_counts():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.xlsx\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: Forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: ForecastSKU, Qty\n\n"
        "Preview (source rows 2-3):\n"
        "| ForecastSKU | Qty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
        "| B2 | 20 |\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.xlsx\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: Actuals\n"
        "Rows: 1\n"
        "Columns: 2\n"
        "Headers: ActualSKU, ActualQty\n\n"
        "Preview (source rows 2):\n"
        "| ActualSKU | ActualQty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
    )

    result = build_excel_workflow_answer(
        "reconcile workbook schemas",
        [
            {"file_id": "forecast", "file_name": "forecast.xlsx", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.xlsx", "text": actuals_summary},
        ],
        [],
    )

    assert result is not None
    assert result["evidence"]["mode"] == "schema_only"
    assert result["evidence"]["table_rows"] == {
        "forecast.xlsx / Forecast": 2,
        "actuals.xlsx / Actuals": 1,
    }


def test_reconcile_reports_duplicate_key_rows_before_comparing():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: forecast\n"
        "Rows: 3\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "A1,12\n"
        "B2,20\n"
        "```\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "B2,20\n"
        "```\n"
    )

    result = build_excel_workflow_answer(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert result is not None
    assert "Duplicate key values found:" in result["answer"]
    assert "`A1` appears 2 times in forecast.csv / forecast rows 2, 3" in result["answer"]
    assert result["evidence"]["duplicate_key_count"] == 1


def test_builds_standalone_local_html_workflow_runtime_for_reconciliation():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "B2,20\n"
        "```\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "B2,25\n"
        "```\n"
    )

    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    assert html.startswith("<!doctype html>")
    assert "Spreadsheet Workflow Automator" in html
    assert "window.__WORKFLOW__" in html
    assert "compareWorkflow" in html
    assert "Download final output CSV" in html
    assert "downloadFinalOutputCsv" in html
    assert "new Blob" in html
    assert "URL.createObjectURL" in html
    assert "reconciliation-output.csv" in html
    assert "function finalOutputRows(workflow)" in html
    assert "key,status,affected_tables,detail,source_refs" in html
    assert "value_difference" in html
    assert "missing_table" in html
    assert "matched" in html
    assert "finalOutputCsv(window.__WORKFLOW__)" in html
    assert '"sharedKey":"SKU"' in html
    assert "B2" in html
    assert "forecast.csv" in html
    assert "actuals.csv" in html
    assert "http://" not in html
    assert "https://" not in html


def test_local_html_app_shows_local_run_instructions_and_workflow_manifest():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU<script>, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU<script>,Qty\n"
        "A1,10\n"
        "B2,20\n"
        "```\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU<script>, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU<script>,Qty\n"
        "A1,10\n"
        "B2,25\n"
        "```\n"
    )

    html = build_excel_workflow_html_app(
        "compare <img src=x onerror=alert(1)> these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    assert "How to run this local app" in html
    assert "Mac:" in html
    assert "Windows:" in html
    assert "Open this generated .html file" in html
    assert "Workflow manifest" in html
    assert "Title: Spreadsheet Workflow Automator" in html
    assert "Question: compare &lt;img src=x onerror=alert(1)&gt; these spreadsheets" in html
    assert "Mode: reconcile" in html
    assert "Shared key: SKU&lt;script&gt;" in html
    assert "Table count: 2" in html
    assert "Workflow contract" in html
    assert "Input files/tables" in html
    assert "forecast.csv / forecast: 2 rows; columns: SKU&lt;script&gt;, Qty" in html
    assert "actuals.csv / actuals: 2 rows; columns: SKU&lt;script&gt;, Qty" in html
    assert "Manual copy/paste/edit steps replaced" in html
    assert "Paste rows from each spreadsheet into one comparison sheet" in html
    assert "Manually scan for value differences on the shared key" in html
    assert "Deterministic transforms" in html
    assert "Normalize and match rows by shared key SKU&lt;script&gt;" in html
    assert "Generate value_difference, missing_table, duplicate_key, and matched statuses" in html
    assert "Final outputs" in html
    assert "reconciliation-output.csv" in html
    assert "http://" not in html
    assert "https://" not in html


def test_local_html_manifest_embeds_workflow_contract_for_generated_app_runtime():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "B2,20\n"
        "```\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "B2,25\n"
        "```\n"
    )

    html = build_excel_workflow_html_app(
        "turn my weekly spreadsheet copy/paste/edit reconciliation into a local HTML app",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    assert '"manualStepsReplaced":["Paste rows from each spreadsheet into one comparison sheet"' in html
    assert '"transforms":["Normalize and match rows by shared key SKU"' in html
    assert '"outputs":["On-screen workflow report","reconciliation-output.csv"]' in html
    assert '"runInstructions":["Save this generated .html file somewhere you control, such as Desktop or Documents."' in html
    assert "const steps = workflow.manualStepsReplaced || [];" in html
    assert "const transforms = workflow.transforms || [];" in html


def test_local_html_manifest_embeds_ordered_runtime_stages_for_dependent_workflow():
    html = build_excel_workflow_html_app(
        "turn my weekly spreadsheet copy/paste/edit reconciliation into a local HTML app",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": "SKU,Qty\nA1,10\nB2,20\n"},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": "SKU,Qty\nA1,12\nC3,30\n"},
        ],
        [],
    )

    assert html is not None
    assert '"runtimeStages":[{"id":"load_inputs","label":"Load embedded input tables","dependsOn":[]}' in html
    assert '{"id":"index_shared_key","label":"Index rows by shared key SKU","dependsOn":["load_inputs"]}' in html
    assert '{"id":"compare_rows","label":"Compare dependent rows and shared columns","dependsOn":["index_shared_key"]}' in html
    assert '{"id":"export_outputs","label":"Render report and CSV output","dependsOn":["compare_rows"]}' in html
    assert "Ordered local runtime stages" in html
    assert "Load embedded input tables" in html
    assert "Compare dependent rows and shared columns" in html
    assert "const stages = workflow.runtimeStages || [];" in html
    assert "container.dataset.runtimeStages = String(stages.length);" in html


def test_schema_only_local_html_workflow_contract_does_not_claim_key_matching():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.xlsx\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: Forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: ForecastSKU, Qty\n\n"
        "Preview (source rows 2-3):\n"
        "| ForecastSKU | Qty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
        "| B2 | 20 |\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.xlsx\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: Actuals\n"
        "Rows: 1\n"
        "Columns: 2\n"
        "Headers: ActualSKU, ActualQty\n\n"
        "Preview (source rows 2):\n"
        "| ActualSKU | ActualQty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
    )

    html = build_excel_workflow_html_app(
        "reconcile workbook schemas",
        [
            {"file_id": "forecast", "file_name": "forecast.xlsx", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.xlsx", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    assert "Mode: schema_only" in html
    assert "Shared key: (none detected)" in html
    assert "Workflow contract" in html
    assert "Display the precomputed schema-only comparison for the uploaded tables" in html
    assert "Generate a schema_only CSV summary row with embedded local JavaScript" in html
    assert "Compare worksheet schemas manually when no shared key is available" in html
    assert "Manually scan for value differences on the shared key" not in html
    assert "Generate value_difference, missing_table, duplicate_key, and matched statuses" not in html


def test_standalone_local_html_runtime_script_is_valid_javascript():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: forecast\n"
        "Rows: 1\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "=A1,10\n"
        "```\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 1\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "=A1,11\n"
        "```\n"
    )

    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = Path(temp_dir) / "workflow-runtime.js"
        script_path.write_text(match.group(1), encoding="utf-8")
        result = subprocess.run(["node", "--check", str(script_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr

        runtime_path = Path(temp_dir) / "workflow-runtime-exec.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
global.window = {};
function element() { return { textContent: '', value: '[]', dataset: {}, append() {}, addEventListener() {} }; }
global.document = {
  getElementById() { return element(); },
  querySelectorAll() { return []; },
  createElement() { return element(); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} };
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
const csv = finalOutputCsv({
  sharedKey: 'SKU',
  tables: [
    { fileName: 'forecast.csv', sheetName: 'forecast', columns: ['SKU', 'Qty'], sourceRows: [2], rows: [{ SKU: '=A1', Qty: '10\\n20' }] },
    { fileName: 'actuals.csv', sheetName: 'actuals', columns: ['SKU', 'Qty'], sourceRows: [2], rows: [{ SKU: '=A1', Qty: '11' }] },
  ],
});
assert(csv.includes('key,status,affected_tables,detail,source_refs'));
assert(csv.includes("'=A1"));
assert(csv.includes('"10\\n20 at forecast.csv / forecast row 2; 11 at actuals.csv / actuals row 2"'));
assert(!csv.includes('tfoo'));
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    assert "String.fromCharCode(10)" in html


def test_local_html_runtime_reports_duplicate_keys_and_escapes_script_end_tags():
    hostile_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast</script>.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "A1,12\n"
        "```\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 1\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "```\n"
    )

    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast</script>.csv", "text": hostile_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    assert "function duplicateKeyWarnings" in html
    assert "const duplicateWarnings = duplicateKeyWarnings(workflow.tables, key);" in html
    assert "Duplicate key values found:" in html
    assert "</script>.csv" not in html
    assert "forecast<\\/script>.csv" in html


def test_local_html_runtime_can_replace_embedded_table_from_pasted_csv():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: forecast\n"
        "Rows: 1\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "```\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 1\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "```\n"
    )

    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    assert "Paste updated CSV/TSV for any input table" in html
    assert "Import pasted CSV rows" in html
    assert "function parseCsvText" in html
    assert "function replaceTableFromCsv" in html
    assert "textarea[data-csv-table]" in html
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-csv-import.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
global.window = {};
function element() { return { textContent: '', value: '[]', dataset: {}, append() {}, addEventListener() {} }; }
global.document = {
  getElementById() { return element(); },
  querySelectorAll() { return []; },
  createElement() { return element(); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} };
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
replaceTableFromCsv(window.__WORKFLOW__, 1, 'SKU,Qty\\nA1,12\\nB2,7\\n');
assert.deepStrictEqual(window.__WORKFLOW__.tables[1].columns, ['SKU', 'Qty']);
assert.deepStrictEqual(window.__WORKFLOW__.tables[1].sourceRows, [2, 3]);
assert.strictEqual(window.__WORKFLOW__.tables[1].rows[0].Qty, '12');
assert(compareWorkflow(window.__WORKFLOW__).includes('A1` differs'));
assert(finalOutputCsv(window.__WORKFLOW__).includes('B2,missing_table'));
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_local_html_runtime_can_import_excel_clipboard_tsv_rows():
    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": "SKU,Qty\nA1,10\nB2,20\n"},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": "SKU,Qty\nA1,10\nB2,20\n"},
        ],
        [],
    )

    assert html is not None
    assert "Paste updated CSV/TSV for any input table" in html
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-tsv-import.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
global.window = {};
function element() { return { textContent: '', value: '[]', dataset: {}, append() {}, addEventListener() {} }; }
global.document = {
  getElementById() { return element(); },
  querySelectorAll() { return []; },
  createElement() { return element(); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} };
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
replaceTableFromCsv(window.__WORKFLOW__, 1, `SKU	Qty
A1	12
C3	30
`);
assert.deepStrictEqual(window.__WORKFLOW__.tables[1].columns, ['SKU', 'Qty']);
assert.deepStrictEqual(window.__WORKFLOW__.tables[1].sourceRows, [2, 3]);
assert(compareWorkflow(window.__WORKFLOW__).includes('A1` differs'));
assert(finalOutputCsv(window.__WORKFLOW__).includes('C3,missing_table'));
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_local_html_editable_csv_roundtrip_preserves_formula_and_negative_values():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: forecast\n"
        "Rows: 1\n"
        "Columns: 3\n"
        "Headers: SKU, Qty, Formula\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty,Formula\n"
        'A1,-5,"=SUM(1,2)"\n'
        "```\n"
    )
    actuals_summary = forecast_summary.replace("forecast.csv", "actuals.csv").replace("## Worksheet: forecast", "## Worksheet: actuals")

    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-editable-csv.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
global.window = {};
function element() { return { textContent: '', value: '[]', dataset: {}, append() {}, addEventListener() {} }; }
global.document = {
  getElementById() { return element(); },
  querySelectorAll() { return []; },
  createElement() { return element(); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} };
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
const editableCsv = tableToCsv({ columns: ['SKU', 'Qty', 'Formula'], rows: [{ SKU: 'A1', Qty: '-5', Formula: '=SUM(1,2)' }] });
assert(editableCsv.includes('A1,-5,"=SUM(1,2)"'));
assert(!editableCsv.includes("'-5"));
assert(!editableCsv.includes("'=SUM"));
replaceTableFromCsv(window.__WORKFLOW__, 0, editableCsv);
assert.strictEqual(window.__WORKFLOW__.tables[0].rows[0].Qty, '-5');
assert.strictEqual(window.__WORKFLOW__.tables[0].rows[0].Formula, '=SUM(1,2)');
const downloadCsv = finalOutputCsv({ sharedKey: 'SKU', tables: [{ fileName: 'a.csv', sheetName: 'a', columns: ['SKU'], sourceRows: [2], rows: [{ SKU: '=A1' }] }] });
assert(downloadCsv.includes("'=A1"));
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_local_html_final_csv_includes_machine_readable_affected_tables_for_multi_file_rows():
    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": "SKU,Qty\nA1,10\nA1,12\nB2,20\n"},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": "SKU,Qty\nA1,11\nC3,30\n"},
        ],
        [],
    )

    assert html is not None
    assert "key,status,affected_tables,detail,source_refs" in html
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-affected-tables.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
global.window = {};
function element() { return { textContent: '', value: '[]', dataset: {}, append() {}, addEventListener() {} }; }
global.document = {
  getElementById() { return element(); },
  querySelectorAll() { return []; },
  createElement() { return element(); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} }
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
const csv = finalOutputCsv(window.__WORKFLOW__);
assert(csv.includes('key,status,affected_tables,detail,source_refs'));
assert(csv.includes(',duplicate_key,forecast.csv / forecast,"`A1` appears 2 times in forecast.csv / forecast rows 2, 3",'));
assert(csv.includes('A1,value_difference,forecast.csv / forecast; actuals.csv / actuals,Qty differs,'));
assert(csv.includes('B2,missing_table,actuals.csv / actuals,missing from actuals.csv / actuals,'));
assert(csv.includes('C3,missing_table,forecast.csv / forecast,missing from forecast.csv / forecast,'));
assert(!csv.includes(',duplicate_key,forecast.csv / forecast; actuals.csv / actuals,'));
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_local_html_csv_import_preserves_physical_source_rows_after_blank_lines():
    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": "SKU,Qty\nA1,10\nB2,20\n"},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": "SKU,Qty\nA1,10\nB2,20\n"},
        ],
        [],
    )

    assert html is not None
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-blank-lines.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
global.window = {};
function element() { return { textContent: '', value: '[]', dataset: {}, append() {}, addEventListener() {} }; }
global.document = {
  getElementById() { return element(); },
  querySelectorAll() { return []; },
  createElement() { return element(); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} };
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
replaceTableFromCsv(window.__WORKFLOW__, 1, 'SKU,Qty\\nA1,10\\n\\nB2,25\\n');
assert.deepStrictEqual(window.__WORKFLOW__.tables[1].sourceRows, [2, 4]);
assert(compareWorkflow(window.__WORKFLOW__).includes('25 at actuals.csv / actuals row 4'));
assert(!compareWorkflow(window.__WORKFLOW__).includes('25 at actuals.csv / actuals row 3'));
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_local_html_csv_import_missing_shared_key_fails_without_replacing_table():
    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": "SKU,Qty\nA1,10\n"},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": "SKU,Qty\nA1,10\n"},
        ],
        [],
    )

    assert html is not None
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-missing-key.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
global.window = {};
function element() { return { textContent: '', value: '[]', dataset: {}, append() {}, addEventListener() {} }; }
global.document = {
  getElementById() { return element(); },
  querySelectorAll() { return []; },
  createElement() { return element(); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} };
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
const beforeRows = JSON.stringify(window.__WORKFLOW__.tables[1].rows);
assert.throws(
  () => replaceTableFromCsv(window.__WORKFLOW__, 1, 'Item,Qty\\nA1,99\\n'),
  /must include shared key column "SKU"/
);
assert.strictEqual(JSON.stringify(window.__WORKFLOW__.tables[1].rows), beforeRows);
assert.deepStrictEqual(window.__WORKFLOW__.tables[1].columns, ['SKU', 'Qty']);
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_local_html_runtime_can_reset_pasted_csv_changes_to_embedded_workflow_data():
    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.csv", "text": "SKU,Qty\nA1,10\nB2,20\n"},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": "SKU,Qty\nA1,10\nB2,20\n"},
        ],
        [],
    )

    assert html is not None
    assert "Reset to embedded data" in html
    assert "function resetWorkflow" in html
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-reset.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
global.window = {};
function element() { return { textContent: '', value: '[]', dataset: {}, append() {}, addEventListener() {} }; }
global.document = {
  getElementById() { return element(); },
  querySelectorAll() { return []; },
  createElement() { return element(); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} };
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
replaceTableFromCsv(window.__WORKFLOW__, 1, 'SKU,Qty\\nA1,12\\nC3,30\\n');
assert(compareWorkflow(window.__WORKFLOW__).includes('A1` differs'));
assert(finalOutputCsv(window.__WORKFLOW__).includes('C3,missing_table'));
resetWorkflow();
assert.deepStrictEqual(window.__WORKFLOW__.tables[1].rows, [{ SKU: 'A1', Qty: '10' }, { SKU: 'B2', Qty: '20' }]);
assert.deepStrictEqual(window.__WORKFLOW__.tables[1].sourceRows, [2, 3]);
assert(compareWorkflow(window.__WORKFLOW__).includes('No key presence or shared-column value differences'));
assert(!finalOutputCsv(window.__WORKFLOW__).includes('C3,missing_table'));
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_local_html_csv_import_skips_unchanged_prepopulated_tables_to_preserve_source_rows():
    forecast_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: forecast.xlsx\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: Forecast\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "Preview (source rows 3, 5):\n"
        "| SKU | Qty |\n"
        "| --- | --- |\n"
        "| A1 | 10 |\n"
        "| B2 | 20 |\n"
    )
    actuals_summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: actuals.csv\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        "## Worksheet: actuals\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Headers: SKU, Qty\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "SKU,Qty\n"
        "A1,10\n"
        "B2,25\n"
        "```\n"
    )

    html = build_excel_workflow_html_app(
        "compare these spreadsheets",
        [
            {"file_id": "forecast", "file_name": "forecast.xlsx", "text": forecast_summary},
            {"file_id": "actuals", "file_name": "actuals.csv", "text": actuals_summary},
        ],
        [],
    )

    assert html is not None
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match is not None
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-skip-unchanged-csv.js"
        runtime_path.write_text(
            """
const assert = require('node:assert');
const elements = new Map();
const csvAreas = [];
const handlers = {};
function element(id) {
  return {
    id,
    textContent: '',
    value: '',
    dataset: {},
    append(...children) { children.forEach(child => { if (child.dataset && child.dataset.csvTable != null) csvAreas.push(child); }); },
    addEventListener(event, handler) { handlers[id + ':' + event] = handler; },
    click() {},
    remove() {},
  };
}
global.window = {};
global.document = {
  getElementById(id) { if (!elements.has(id)) elements.set(id, element(id)); return elements.get(id); },
  querySelectorAll(selector) { return selector === 'textarea[data-csv-table]' ? csvAreas : []; },
  createElement(tag) { return element(tag); },
  body: { appendChild() {} },
};
global.URL = { createObjectURL() { return 'blob:local'; }, revokeObjectURL() {} };
global.Blob = class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } };
"""
            + match.group(1)
            + """
assert.deepStrictEqual(window.__WORKFLOW__.tables[0].sourceRows, [3, 5]);
assert.strictEqual(csvAreas.length, 2);
assert.strictEqual(csvAreas[0].value, csvAreas[0].dataset.originalCsv);
handlers['import-csv:click']();
assert.deepStrictEqual(window.__WORKFLOW__.tables[0].sourceRows, [3, 5]);
assert(compareWorkflow(window.__WORKFLOW__).includes('20 at forecast.xlsx / Forecast row 5'));
assert(!compareWorkflow(window.__WORKFLOW__).includes('20 at forecast.xlsx / Forecast row 3'));
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
