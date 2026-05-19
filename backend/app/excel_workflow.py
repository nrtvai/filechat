from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any

from .survey import ParsedTable, parse_table

WORKFLOW_TERMS = (
    "compare",
    "reconcile",
    "match",
    "mismatch",
    "variance",
    "differences",
    "discrepanc",
    "cross-file",
    "across files",
    "workbook",
    "spreadsheet workflow",
    "excel mode",
)

KEY_HINTS = ("sku", "id", "item", "product", "account", "customer", "order", "name")


@dataclass
class WorkflowTable:
    file_id: str
    file_name: str
    sheet_name: str
    rows: list[dict[str, str]]
    columns: list[str]
    source_rows: list[int] | None = None
    source_id: int | None = None


def is_excel_workflow_request(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in WORKFLOW_TERMS)


def build_excel_workflow_answer(question: str, file_texts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build a deterministic multi-spreadsheet compare/reconcile answer.

    This is intentionally local-first and non-LLM: it parses the extracted Excel
    Mode summaries/CSV raw data already stored for the session, compares tables
    on a shared key, and emits row-level provenance in the answer text.
    """

    if not is_excel_workflow_request(question):
        return None
    tables = _workflow_tables(file_texts, sources)
    if len(tables) < 2:
        return None

    key = _shared_key(tables)
    if not key:
        answer = _schema_only_answer(tables)
        return {
            "answer": answer,
            "cited_source_ids": _cited_source_ids(tables),
            "evidence": {"mode": "schema_only", "table_rows": _table_row_counts(tables)},
        }

    indexed = [_index_by_key(table, key) for table in tables]
    all_keys = sorted(set().union(*(set(item) for item in indexed)))
    rows: list[str] = [
        "Excel Mode reconciliation (local deterministic workflow)",
        f"Compared {len(tables)} spreadsheet tables on shared key `{key}`.",
        "Parsed table scope: " + "; ".join(f"{_table_ref(table)} ({len(table.rows)} rows)" for table in tables) + ".",
        "",
    ]
    duplicate_key_warnings = _duplicate_key_warnings(tables, key)
    if duplicate_key_warnings:
        rows.append("Duplicate key values found:")
        rows.extend(f"- {warning}" for warning in duplicate_key_warnings)
        rows.append("")
    issue_count = 0
    for value in all_keys:
        present = [(table, row_info) for table, index in zip(tables, indexed) if (row_info := index.get(value))]
        if len(present) != len(tables):
            issue_count += 1
            missing = [table for table, index in zip(tables, indexed) if value not in index]
            rows.append(
                f"- `{value}` is missing from "
                + ", ".join(_table_ref(table) for table in missing)
                + "; present in "
                + ", ".join(_row_ref(table, info[1]) for table, info in present)
                + "."
            )
            continue
        comparable = _comparable_columns([table for table, _ in present], key)
        diffs: list[str] = []
        for column in comparable:
            values = [(table, info[0].get(column, ""), info[1]) for table, info in present]
            normalized = {value.strip() for _table, value, _row_number in values}
            if len(normalized) > 1:
                diffs.append(
                    f"{column}: "
                    + "; ".join(f"{value or '(blank)'} at {_row_ref(table, row_number)}" for table, value, row_number in values)
                )
        if diffs:
            issue_count += 1
            rows.append(f"- `{value}` differs — " + " | ".join(diffs) + ".")

    if issue_count == 0:
        rows.append("No key presence or shared-column value differences were found in the parsed rows.")
    rows.extend([
        "",
        "Provenance uses file/sheet/row references from the uploaded spreadsheets. Only parsed local spreadsheet data was used.",
    ])
    return {
        "answer": "\n".join(rows),
        "cited_source_ids": _cited_source_ids(tables),
        "evidence": {
            "mode": "reconcile",
            "key": key,
            "table_count": len(tables),
            "issue_count": issue_count,
            "duplicate_key_count": len(duplicate_key_warnings),
            "table_rows": _table_row_counts(tables),
        },
    }


def _workflow_tables(file_texts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[WorkflowTable]:
    by_file_source: dict[str, int] = {}
    for source in sources:
        file_id = str(source.get("file_id") or "")
        if file_id and source.get("source_id") is not None and file_id not in by_file_source:
            by_file_source[file_id] = int(source["source_id"])

    tables: list[WorkflowTable] = []
    for item in file_texts:
        file_id = str(item.get("file_id") or "")
        file_name = str(item.get("file_name") or "spreadsheet")
        text = str(item.get("text") or "")
        extracted = _tables_from_excel_summary(text, file_id, file_name)
        if not extracted:
            parsed = parse_table(text, file_id=file_id, file_name=file_name)
            if parsed:
                extracted = [_from_parsed(parsed, _default_sheet_name(file_name))]
        for table in extracted:
            table.source_id = by_file_source.get(table.file_id)
            tables.append(table)
    return tables


def _tables_from_excel_summary(text: str, file_id: str, file_name: str) -> list[WorkflowTable]:
    if "Excel Mode Spreadsheet Summary" not in text:
        return []
    tables: list[WorkflowTable] = []
    sections = re.split(r"(?=^## Worksheet: )", text, flags=re.MULTILINE)
    for section in sections:
        match = re.search(r"^## Worksheet: (?P<sheet>.+)$", section, flags=re.MULTILINE)
        if not match:
            continue
        sheet_name = match.group("sheet").strip()
        raw = re.search(r"## Raw Data \((?:CSV|TSV)\)\s*\n```(?:csv|tsv)\s*\n(?P<body>.*?)\n```", section, re.DOTALL | re.IGNORECASE)
        source_rows: list[int] | None = None
        if raw:
            table_text = raw.group("body")
        else:
            table_text, source_rows = _preview_table_to_csv_and_source_rows(section)
        if not table_text.strip():
            continue
        parsed, raw_source_rows = _parse_csv_text(table_text, file_id=file_id, file_name=file_name)
        if raw and raw_source_rows is not None:
            source_rows = raw_source_rows
        if parsed:
            tables.append(_from_parsed(parsed, sheet_name, source_rows=source_rows))
    return tables


def _preview_table_to_csv(section: str) -> str:
    table_text, _source_rows = _preview_table_to_csv_and_source_rows(section)
    return table_text


def _preview_table_to_csv_and_source_rows(section: str) -> tuple[str, list[int] | None]:
    lines = section.splitlines()
    out_rows: list[list[str]] = []
    source_rows: list[int] | None = None
    in_table = False
    for line in lines:
        stripped = line.strip()
        if _is_preview_label(stripped):
            source_rows = _parse_preview_source_rows(stripped)
            in_table = True
            continue
        if not in_table:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip().replace("\\|", "|") for cell in stripped.strip("|").split("|")]
            if cells and all(cell.replace("-", "").strip() == "" for cell in cells):
                continue
            out_rows.append(cells)
            continue
        if out_rows:
            break
    if len(out_rows) < 2:
        return "", None
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(out_rows)
    data_row_count = len(out_rows) - 1
    if source_rows is not None:
        source_rows = source_rows[:data_row_count] if len(source_rows) >= data_row_count else None
    return output.getvalue(), source_rows


def _is_preview_label(value: str) -> bool:
    return value == "Preview:" or (value.startswith("Preview (") and value.endswith(":"))


def _parse_preview_source_rows(label: str) -> list[int] | None:
    match = re.search(r"source rows (?P<rows>[^)]+)", label)
    if not match:
        return None
    parsed: list[int] = []
    for part in match.group("rows").split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            try:
                start = int(start_text.strip())
                end = int(end_text.strip())
            except ValueError:
                return None
            if end < start:
                return None
            parsed.extend(range(start, end + 1))
            continue
        try:
            parsed.append(int(token))
        except ValueError:
            return None
    return parsed or None


def _parse_csv_text(text: str, *, file_id: str, file_name: str) -> tuple[ParsedTable | None, list[int] | None]:
    cleaned = text.strip()
    delimiter = "\t" if "\t" in cleaned.splitlines()[0] and cleaned.splitlines()[0].count("\t") > cleaned.splitlines()[0].count(",") else ","
    reader = csv.DictReader(io.StringIO(cleaned), delimiter=delimiter)
    if not reader.fieldnames:
        return None, None
    columns = [str(column or "").strip() for column in reader.fieldnames]
    rows = []
    source_rows: list[int] = []
    for raw in reader:
        row = {column: str(raw.get(column) or "").strip() for column in columns}
        if any(row.values()):
            rows.append(row)
            source_rows.append(reader.line_num)
    parsed = ParsedTable(file_id=file_id, file_name=file_name, rows=rows, columns=columns, delimiter=delimiter) if rows else None
    return parsed, source_rows or None


def _from_parsed(parsed: ParsedTable, sheet_name: str, *, source_rows: list[int] | None = None) -> WorkflowTable:
    return WorkflowTable(
        file_id=parsed.file_id,
        file_name=parsed.file_name,
        sheet_name=sheet_name,
        rows=parsed.rows,
        columns=parsed.columns,
        source_rows=source_rows,
    )


def _default_sheet_name(file_name: str) -> str:
    return re.sub(r"\.[^.]+$", "", file_name) or "Sheet1"


def _shared_key(tables: list[WorkflowTable]) -> str | None:
    common = set(tables[0].columns)
    for table in tables[1:]:
        common &= set(table.columns)
    if not common:
        return None
    lowered = {column.lower(): column for column in common}
    for hint in KEY_HINTS:
        for lower, original in lowered.items():
            if lower == hint or lower.endswith("_" + hint) or hint in lower:
                return original
    return sorted(common, key=lambda col: (len(col), col.lower()))[0]


def _index_by_key(table: WorkflowTable, key: str) -> dict[str, tuple[dict[str, str], int]]:
    index: dict[str, tuple[dict[str, str], int]] = {}
    for idx, row in enumerate(table.rows, start=2):  # header is row 1
        value = row.get(key, "").strip()
        if value and value not in index:
            source_row = table.source_rows[idx - 2] if table.source_rows and idx - 2 < len(table.source_rows) else idx
            index[value] = (row, source_row)
    return index


def _duplicate_key_warnings(tables: list[WorkflowTable], key: str) -> list[str]:
    warnings: list[str] = []
    for table in tables:
        rows_by_key: dict[str, list[int]] = {}
        for idx, row in enumerate(table.rows, start=2):  # header is row 1
            value = row.get(key, "").strip()
            if not value:
                continue
            source_row = table.source_rows[idx - 2] if table.source_rows and idx - 2 < len(table.source_rows) else idx
            rows_by_key.setdefault(value, []).append(source_row)
        for value in sorted(rows_by_key):
            source_rows = rows_by_key[value]
            if len(source_rows) > 1:
                warnings.append(
                    f"`{value}` appears {len(source_rows)} times in {_table_ref(table)} rows "
                    + ", ".join(str(row_number) for row_number in source_rows)
                )
    return warnings


def _comparable_columns(tables: list[WorkflowTable], key: str) -> list[str]:
    common = set(tables[0].columns)
    for table in tables[1:]:
        common &= set(table.columns)
    return [column for column in tables[0].columns if column in common and column != key]


def _cited_source_ids(tables: list[WorkflowTable]) -> list[int]:
    return list(dict.fromkeys(table.source_id for table in tables if table.source_id is not None))


def _table_row_counts(tables: list[WorkflowTable]) -> dict[str, int]:
    return {_table_ref(table): len(table.rows) for table in tables}


def _table_ref(table: WorkflowTable) -> str:
    return f"{table.file_name} / {table.sheet_name}"


def _row_ref(table: WorkflowTable, row_number: int) -> str:
    return f"{table.file_name} / {table.sheet_name} row {row_number}"


def _schema_only_answer(tables: list[WorkflowTable]) -> str:
    lines = [
        "Excel Mode reconciliation (local deterministic workflow)",
        "I could not find a shared key column across the parsed spreadsheets, so I compared schemas instead.",
        "",
    ]
    for table in tables:
        lines.append(f"- {_table_ref(table)}: {len(table.rows)} rows; columns: {', '.join(table.columns)}")
    return "\n".join(lines)
