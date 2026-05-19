from __future__ import annotations

from backend.app.excel_workflow import build_excel_workflow_answer


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
