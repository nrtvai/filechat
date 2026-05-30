from __future__ import annotations

import json
import re

from backend.app.excel_workflow import build_excel_workflow_html_app


def _workflow_summary(workbook: str, worksheet: str, raw_csv: str) -> str:
    headers = raw_csv.splitlines()[0]
    row_count = max(0, len([line for line in raw_csv.splitlines()[1:] if line.strip()]))
    column_count = len(headers.split(","))
    header_cells = " | ".join(headers.split(","))
    separator = " | ".join("---" for _ in headers.split(","))
    preview_rows = "\n".join(
        "| " + " | ".join(line.split(",")) + " |" for line in raw_csv.splitlines()[1:4] if line.strip()
    )
    return (
        "# Excel Mode Spreadsheet Summary\n\n"
        f"Workbook: {workbook}\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        f"## Worksheet: {worksheet}\n"
        f"Rows: {row_count}\n"
        f"Columns: {column_count}\n"
        f"Headers: {headers.replace(',', ', ')}\n\n"
        "Preview (source rows 2-4):\n"
        f"| {header_cells} |\n"
        f"| {separator} |\n"
        f"{preview_rows}\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        f"{raw_csv.rstrip()}\n"
        "```\n"
    )


def _workflow_payload(html: str) -> dict:
    match = re.search(r"window\.__WORKFLOW__ = (?P<payload>\{.*?\});", html)
    assert match, "generated HTML should embed a workflow manifest payload"
    return json.loads(match.group("payload"))


def test_local_html_app_embeds_dependent_file_workflow_graph() -> None:
    html = build_excel_workflow_html_app(
        "Interview me and automate this copy/paste spreadsheet workflow as a local HTML app",
        [
            {
                "file_id": "forecast",
                "file_name": "forecast.csv",
                "text": _workflow_summary("forecast.csv", "forecast", "SKU,Qty\nA1,10\nB2,20\n"),
            },
            {
                "file_id": "actuals",
                "file_name": "actuals.csv",
                "text": _workflow_summary("actuals.csv", "actuals", "SKU,Qty\nA1,12\nC3,30\n"),
            },
        ],
        [],
    )

    assert html is not None
    payload = _workflow_payload(html)

    assert "Dependent file workflow graph" in html
    assert payload["dependencyGraph"] == [
        {"id": "input_1", "label": "forecast.csv / forecast", "kind": "input", "dependsOn": []},
        {"id": "input_2", "label": "actuals.csv / actuals", "kind": "input", "dependsOn": []},
        {"id": "match_shared_key", "label": "Match rows by SKU", "kind": "transform", "dependsOn": ["input_1", "input_2"]},
        {"id": "final_output", "label": "reconciliation-output.csv", "kind": "output", "dependsOn": ["match_shared_key"]},
    ]


def test_local_html_runtime_rejects_pasted_csv_with_duplicate_headers() -> None:
    html = build_excel_workflow_html_app(
        "Compare these spreadsheets and generate a local HTML app",
        [
            {
                "file_id": "forecast",
                "file_name": "forecast.csv",
                "text": _workflow_summary("forecast.csv", "forecast", "SKU,Qty\nA1,10\n"),
            },
            {
                "file_id": "actuals",
                "file_name": "actuals.csv",
                "text": _workflow_summary("actuals.csv", "actuals", "SKU,Qty\nA1,10\n"),
            },
        ],
        [],
    )

    assert html is not None
    assert "function validateImportedCsv" in html
    script = re.search(r"<script>([\s\S]*)</script>", html)
    assert script is not None

    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_path = Path(temp_dir) / "workflow-runtime-duplicate-headers.js"
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
            + script.group(1)
            + """
const before = JSON.stringify(window.__WORKFLOW__.tables[1]);
assert.throws(
  () => replaceTableFromCsv(window.__WORKFLOW__, 1, 'SKU,Qty,Qty\\nA1,10,11\\n'),
  /duplicate column header "Qty"/
);
assert.strictEqual(JSON.stringify(window.__WORKFLOW__.tables[1]), before);
""",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(runtime_path)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
