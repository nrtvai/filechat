from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SPREADSHEET_EXTENSIONS = {"csv", "tsv", "xls", "xlsx"}
MAX_PREVIEW_ROWS = 5
MAX_FORMULAS = 20
MAX_HEADERS = 24


class SpreadsheetModeError(RuntimeError):
    """User-facing spreadsheet parser error with normalized wording."""


def is_spreadsheet_file(ext: str) -> bool:
    return ext.lower().lstrip(".") in SPREADSHEET_EXTENSIONS


@dataclass
class WorksheetSummary:
    name: str
    rows: int
    columns: int
    headers: list[str] = field(default_factory=list)
    preview_rows: list[list[str]] = field(default_factory=list)
    preview_source_rows: list[int] = field(default_factory=list)
    preview_start_row: int | None = None
    preview_end_row: int | None = None
    formulas: list[str] = field(default_factory=list)
    raw_delimited: str = ""


def spreadsheet_mode_summary(path: Path, ext: str, *, display_name: str | None = None) -> str:
    normalized_ext = ext.lower().lstrip(".")
    workbook_name = display_name or path.name
    try:
        if normalized_ext in {"csv", "tsv"}:
            worksheets = [_summarize_delimited(path, delimiter="\t" if normalized_ext == "tsv" else ",")]
        elif normalized_ext == "xlsx":
            worksheets = _summarize_workbook(path)
        elif normalized_ext == "xls":
            raise SpreadsheetModeError(f"Unsupported spreadsheet type for {workbook_name}: xls")
        else:
            raise SpreadsheetModeError(f"Unsupported spreadsheet type for {workbook_name}: {ext}")
    except SpreadsheetModeError as exc:
        if str(exc).startswith("Could not extract spreadsheet summary"):
            raise _spreadsheet_parse_error(workbook_name, exc) from exc
        raise
    except Exception as exc:  # pragma: no cover - exercised through corrupt workbook integration
        raise _spreadsheet_parse_error(workbook_name, exc) from exc

    if display_name and normalized_ext in {"csv", "tsv"} and worksheets:
        worksheets[0].name = Path(display_name).stem
    return _render_summary(workbook_name, worksheets)


def _spreadsheet_parse_error(workbook_name: str, exc: Exception) -> SpreadsheetModeError:
    detail = str(exc)
    prefix = "Could not extract spreadsheet summary"
    if detail.startswith(f"{prefix} for "):
        return SpreadsheetModeError(detail)
    if detail.startswith(f"{prefix}:"):
        detail = detail.split(":", 1)[1].strip()
    return SpreadsheetModeError(f"Could not extract spreadsheet summary for {workbook_name}: {detail}")


def extract_table_text_from_spreadsheet_summary(text: str) -> str:
    """Return raw table text embedded in an Excel Mode summary.

    Non-Excel-Mode text is returned unchanged. For summaries, prefer the
    explicit Raw Data fence, and fall back to converting the Preview markdown
    table into CSV-compatible text.
    """

    if "Excel Mode Spreadsheet Summary" not in text:
        return text
    raw = re.search(r"## Raw Data \((?:CSV|TSV)\)\s*\n```(?:csv|tsv)\s*\n(?P<body>.*?)\n```", text, flags=re.DOTALL | re.IGNORECASE)
    if raw:
        return raw.group("body")
    preview = _extract_preview_table_as_csv(text)
    return preview or text


def _summarize_delimited(path: Path, *, delimiter: str) -> WorksheetSummary:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            raw_text = handle.read()
            rows = list(csv.reader(raw_text.splitlines(), delimiter=delimiter))
    except Exception as exc:
        raise SpreadsheetModeError(f"Could not extract spreadsheet summary: {exc}") from exc

    if not rows:
        return WorksheetSummary(name=path.stem, rows=0, columns=0)

    headers = [_cell_to_text(cell) for cell in rows[0]]
    preview: list[list[str]] = []
    preview_source_rows: list[int] = []
    non_empty_data_rows = 0
    max_column = _last_non_empty_index(headers)
    for source_row_number, row in enumerate(rows[1:], start=2):
        values = [_cell_to_text(cell) for cell in row]
        if not any(value != "" for value in values):
            continue
        non_empty_data_rows += 1
        max_column = max(max_column, _last_non_empty_index(values))
        if len(preview) < MAX_PREVIEW_ROWS:
            preview.append(values)
            preview_source_rows.append(source_row_number)
    return WorksheetSummary(
        name=path.stem,
        rows=non_empty_data_rows,
        columns=max_column,
        headers=headers[:MAX_HEADERS],
        preview_rows=preview,
        preview_source_rows=preview_source_rows,
        preview_start_row=preview_source_rows[0] if preview_source_rows else None,
        preview_end_row=preview_source_rows[-1] if preview_source_rows else None,
        raw_delimited=raw_text.strip(),
    )


def _summarize_workbook(path: Path) -> list[WorksheetSummary]:
    try:
        import openpyxl
    except Exception as exc:  # pragma: no cover - depends on environment packaging
        raise SpreadsheetModeError("Could not extract spreadsheet summary: openpyxl is required for XLSX files") from exc

    try:
        workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    except Exception as exc:
        raise SpreadsheetModeError(f"Could not extract spreadsheet summary: {exc}") from exc

    summaries: list[WorksheetSummary] = []
    try:
        for worksheet in workbook.worksheets:
            rows_iter = worksheet.iter_rows(values_only=False)
            first_row = next(rows_iter, None)
            headers = [_cell_to_text(cell.value) for cell in first_row] if first_row else []
            preview_rows: list[list[str]] = []
            preview_source_rows: list[int] = []
            formulas: list[str] = []
            non_empty_data_rows = 0
            max_column = 0

            for worksheet_row_number, row in enumerate(rows_iter, start=2):
                values = [_cell_to_text(cell.value) for cell in row]
                if any(value != "" for value in values):
                    non_empty_data_rows += 1
                    max_column = max(max_column, _last_non_empty_index(values))
                    if len(preview_rows) < MAX_PREVIEW_ROWS:
                        preview_rows.append(values)
                        preview_source_rows.append(worksheet_row_number)
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.startswith("=") and len(formulas) < MAX_FORMULAS:
                        formulas.append(f"{worksheet.title}!{cell.coordinate} = {cell.value}")

            header_width = _last_non_empty_index(headers)
            summaries.append(
                WorksheetSummary(
                    name=worksheet.title,
                    rows=non_empty_data_rows,
                    columns=max(header_width, max_column),
                    headers=headers[:MAX_HEADERS],
                    preview_rows=preview_rows,
                    preview_source_rows=preview_source_rows,
                    preview_start_row=preview_source_rows[0] if preview_source_rows else None,
                    preview_end_row=preview_source_rows[-1] if preview_source_rows else None,
                    formulas=formulas,
                )
            )
    finally:
        workbook.close()
    return summaries


def _render_summary(workbook_name: str, worksheets: Iterable[WorksheetSummary]) -> str:
    lines = [
        "# Excel Mode Spreadsheet Summary",
        "",
        f"Workbook: {workbook_name}",
        "Mode: Excel / spreadsheet analysis lane",
        "",
    ]
    for sheet in worksheets:
        lines.extend(
            [
                f"## Worksheet: {sheet.name}",
                f"Rows: {sheet.rows}",
                f"Columns: {sheet.columns}",
            ]
        )
        if sheet.headers:
            lines.append(f"Headers: {', '.join(sheet.headers)} (source row 1)")
        if sheet.preview_rows:
            lines.extend(["", _preview_label(sheet), _markdown_table(sheet.headers, sheet.preview_rows)])
        if sheet.formulas:
            lines.extend(["", "Formulas:"])
            lines.extend(f"- {formula}" for formula in sheet.formulas)
        lines.append("")
        if sheet.raw_delimited:
            fence = "tsv" if "\t" in sheet.raw_delimited.splitlines()[0] else "csv"
            lines.extend([f"## Raw Data ({fence.upper()})", f"```{fence}", sheet.raw_delimited, "```", ""])
    lines.append(
        "Use this summary for grounded spreadsheet questions; cite worksheet names, headers, previews, and formulas instead of inventing cells."
    )
    return "\n".join(lines).strip() + "\n"


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    width = max(len(headers), *(len(row) for row in rows)) if rows else len(headers)
    normalized_headers = [*headers[:width], *[f"Column {i}" for i in range(len(headers) + 1, width + 1)]]
    if not normalized_headers:
        normalized_headers = ["Value"]
        width = 1
    lines = [
        "| " + " | ".join(_escape_table_cell(cell) for cell in normalized_headers) + " |",
        "| " + " | ".join("---" for _ in normalized_headers) + " |",
    ]
    for row in rows:
        normalized_row = [*row[:width], *[""] * max(0, width - len(row))]
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in normalized_row) + " |")
    return "\n".join(lines)


def _extract_preview_table_as_csv(text: str) -> str:
    lines = text.splitlines()
    preview_tables: list[list[str]] = []
    table_lines: list[str] = []
    in_preview = False
    for line in lines:
        stripped = line.strip()
        if _is_preview_label(stripped):
            if table_lines:
                preview_tables.append(table_lines)
                table_lines = []
            in_preview = True
            continue
        if not in_preview:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines.append(stripped)
            continue
        if table_lines:
            preview_tables.append(table_lines)
            table_lines = []
        in_preview = False
    if table_lines:
        preview_tables.append(table_lines)

    candidates: list[tuple[int, str]] = []
    for preview_table in preview_tables:
        rows = _markdown_table_lines_to_rows(preview_table)
        if len(rows) < 2:
            continue
        width = max((len(row) for row in rows), default=0)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(rows)
        # Prefer wider, data-bearing worksheet previews so a leading notes sheet
        # does not hide the actual spreadsheet table from artifact builders.
        candidates.append((width * 1000 + len(rows), output.getvalue()))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _markdown_table_lines_to_rows(table_lines: list[str]) -> list[list[str]]:
    if len(table_lines) < 3:
        return []
    rows: list[list[str]] = []
    for line in table_lines:
        cells = [cell.strip().replace("\\|", "|") for cell in line.strip("|").split("|")]
        if cells and all(cell.replace("-", "").strip() == "" for cell in cells):
            continue
        rows.append(cells)
    return rows


def _cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _preview_label(sheet: WorksheetSummary) -> str:
    if sheet.preview_source_rows:
        return f"Preview (source rows {_format_source_rows(sheet.preview_source_rows)}):"
    if sheet.preview_start_row is not None and sheet.preview_end_row is not None:
        return f"Preview (source rows {sheet.preview_start_row}-{sheet.preview_end_row}):"
    return "Preview:"


def _format_source_rows(row_numbers: list[int]) -> str:
    ranges: list[str] = []
    start = previous = row_numbers[0]
    for row_number in row_numbers[1:]:
        if row_number == previous + 1:
            previous = row_number
            continue
        ranges.append(_format_source_row_range(start, previous))
        start = previous = row_number
    ranges.append(_format_source_row_range(start, previous))
    return ", ".join(ranges)


def _format_source_row_range(start: int, end: int) -> str:
    if start == end:
        return str(start)
    return f"{start}-{end}"


def _is_preview_label(value: str) -> bool:
    return value == "Preview:" or (value.startswith("Preview (") and value.endswith(":"))


def _last_non_empty_index(values: list[str]) -> int:
    for index in range(len(values), 0, -1):
        if values[index - 1] != "":
            return index
    return 0


def _escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
