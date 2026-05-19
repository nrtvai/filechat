from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.spreadsheet_mode import (
    SpreadsheetModeError,
    extract_table_text_from_spreadsheet_summary,
    is_spreadsheet_file,
    spreadsheet_mode_summary,
)
from backend.app.survey import parse_table


def test_detects_spreadsheet_extensions_case_insensitively():
    assert is_spreadsheet_file("csv")
    assert is_spreadsheet_file("XLSX")
    assert is_spreadsheet_file("tsv")
    assert not is_spreadsheet_file("pdf")


def test_extract_table_text_returns_non_excel_text_unchanged():
    text = "Name,Value\nA,1\n"

    assert extract_table_text_from_spreadsheet_summary(text) == text


def test_extract_table_text_prefers_raw_data_from_excel_mode_summary():
    summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: sales.csv\n"
        "Preview:\n"
        "| Region | Revenue |\n"
        "| --- | --- |\n"
        "| PreviewOnly | 0 |\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "Region,Revenue\n"
        "North,1200\n"
        "```\n"
    )

    assert extract_table_text_from_spreadsheet_summary(summary) == "Region,Revenue\nNorth,1200"


def test_extract_table_text_converts_excel_mode_preview_table_when_raw_data_is_absent():
    summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: sales.xlsx\n"
        "Preview:\n"
        "| Region | Revenue |\n"
        "| --- | --- |\n"
        "| North | 1200 |\n"
        "| South | 900 |\n"
    )

    assert extract_table_text_from_spreadsheet_summary(summary) == "Region,Revenue\r\nNorth,1200\r\nSouth,900\r\n"


def test_csv_summary_is_excel_lane_artifact_with_columns_and_sample(tmp_path: Path):
    csv_path = tmp_path / "quarterly_sales.csv"
    csv_path.write_text("Region,Revenue,Margin\nNorth,1200,0.32\nSouth,900,0.27\n", encoding="utf-8")

    summary = spreadsheet_mode_summary(csv_path, "csv")

    assert summary.startswith("# Excel Mode Spreadsheet Summary")
    assert "Workbook: quarterly_sales.csv" in summary
    assert "Worksheet: quarterly_sales" in summary
    assert "Rows: 2" in summary
    assert "Columns: 3" in summary
    assert "Headers: Region, Revenue, Margin" in summary
    assert "| Region | Revenue | Margin |" in summary
    assert "| North | 1200 | 0.32 |" in summary
    assert "## Raw Data (CSV)" in summary
    assert "Region,Revenue,Margin" in summary


def test_csv_summary_labels_headers_with_source_row_one(tmp_path: Path):
    csv_path = tmp_path / "quarterly_sales.csv"
    csv_path.write_text("Region,Revenue\nNorth,1200\n", encoding="utf-8")

    summary = spreadsheet_mode_summary(csv_path, "csv")

    assert "Headers: Region, Revenue (source row 1)" in summary


def test_csv_summary_labels_preview_with_source_row_range(tmp_path: Path):
    csv_path = tmp_path / "quarterly_sales.csv"
    csv_path.write_text(
        "Region,Revenue\nNorth,1200\nSouth,900\nWest,700\nEast,650\nCentral,500\nOnline,450\n",
        encoding="utf-8",
    )

    summary = spreadsheet_mode_summary(csv_path, "csv")

    assert "Preview (source rows 2-6):" in summary
    assert "Rows: 6" in summary
    assert "| Online | 450 |" not in summary


def test_csv_summary_skips_blank_rows_in_preview_and_row_count(tmp_path: Path):
    csv_path = tmp_path / "gapped_sales.csv"
    csv_path.write_text(
        "Region,Revenue\nNorth,1200\n\nSouth,900\nWest,700\nEast,650\nCentral,500\nOnline,450\n",
        encoding="utf-8",
    )

    summary = spreadsheet_mode_summary(csv_path, "csv")

    assert "Rows: 6" in summary
    assert "Preview (source rows 2, 4-7):" in summary
    assert "| North | 1200 |" in summary
    assert "| South | 900 |" in summary
    assert "|  |" not in summary
    assert "| Online | 450 |" not in summary


def test_parse_table_uses_raw_data_from_excel_mode_csv_summary():
    summary = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: sales.csv\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        "Region,Revenue\n"
        "North,1200\n"
        "```\n"
    )

    table = parse_table(summary, file_id="file_1", file_name="sales.csv")

    assert table is not None
    assert table.columns == ["Region", "Revenue"]
    assert table.rows[0]["Region"] == "North"


def test_parse_table_uses_excel_mode_markdown_preview_when_raw_data_is_absent():
    summary_chunk = (
        "# Excel Mode Spreadsheet Summary\n\n"
        "Workbook: sales.csv\n"
        "## Worksheet: sales\n"
        "Headers: Region, Revenue\n\n"
        "Preview:\n"
        "| Region | Revenue |\n"
        "| --- | --- |\n"
        "| North | 1200 |\n"
        "| South | 900 |\n"
    )

    table = parse_table(summary_chunk, file_id="file_1", file_name="sales.csv")

    assert table is not None
    assert table.columns == ["Region", "Revenue"]
    assert table.rows[1]["Revenue"] == "900"


def test_xlsx_summary_reports_worksheets_dimensions_and_formulas(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "budget.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws.append(["Month", "Revenue", "Cost", "Profit"])
    ws.append(["Jan", 1000, 400, "=B2-C2"])
    ws.append(["Feb", 1200, 450, "=B3-C3"])
    model = wb.create_sheet("Assumptions")
    model["A1"] = "Tax Rate"
    model["B1"] = 0.2
    wb.save(workbook_path)

    summary = spreadsheet_mode_summary(workbook_path, "xlsx")

    assert "Workbook: budget.xlsx" in summary
    assert "Worksheet: Plan" in summary
    assert "Rows: 2" in summary
    assert "Columns: 4" in summary
    assert "Headers: Month, Revenue, Cost, Profit" in summary
    assert "Preview (source rows 2-3):" in summary
    assert "Formulas:" in summary
    assert "Plan!D2 = =B2-C2" in summary
    assert "Plan!D3 = =B3-C3" in summary
    assert "Worksheet: Assumptions" in summary


def test_xlsx_summary_labels_preview_with_actual_gapped_source_rows(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "gapped.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gaps"
    ws.append(["Name", "Value"])
    ws["A3"] = "Actual row 3"
    ws["B3"] = 30
    ws["A5"] = "Actual row 5"
    ws["B5"] = 50
    ws["A6"] = "Actual row 6"
    ws["B6"] = 60
    ws["A7"] = "Actual row 7"
    ws["B7"] = 70
    wb.save(workbook_path)

    summary = spreadsheet_mode_summary(workbook_path, "xlsx")

    assert "Rows: 4" in summary
    assert "Preview (source rows 3, 5-7):" in summary
    assert "Preview (source rows 2-5):" not in summary
    assert "| Actual row 3 | 30 |" in summary
    assert "| Actual row 5 | 50 |" in summary


def test_unsupported_spreadsheet_type_error_names_workbook(tmp_path: Path):
    unsupported = tmp_path / "legacy.xls"
    unsupported.write_text("old binary excel placeholder", encoding="utf-8")

    with pytest.raises(SpreadsheetModeError) as excinfo:
        spreadsheet_mode_summary(unsupported, "xls", display_name="client_legacy.xls")

    assert str(excinfo.value) == "Unsupported spreadsheet type for client_legacy.xls: xls"


def test_spreadsheet_parser_errors_are_normalized_with_workbook_name(tmp_path: Path):
    broken = tmp_path / "broken.xlsx"
    broken.write_text("not a workbook", encoding="utf-8")

    with pytest.raises(SpreadsheetModeError) as excinfo:
        spreadsheet_mode_summary(broken, "xlsx")

    message = str(excinfo.value)
    assert message.startswith("Could not extract spreadsheet summary for broken.xlsx:")
    assert "not a workbook" in message.lower() or "zip" in message.lower()


def test_spreadsheet_parser_errors_use_display_name_for_upload_context(tmp_path: Path):
    broken = tmp_path / "tmp-upload-body"
    broken.write_text("not a workbook", encoding="utf-8")

    with pytest.raises(SpreadsheetModeError) as excinfo:
        spreadsheet_mode_summary(broken, "xlsx", display_name="client_budget.xlsx")

    message = str(excinfo.value)
    assert message.startswith("Could not extract spreadsheet summary for client_budget.xlsx:")
    assert "tmp-upload-body" not in message
