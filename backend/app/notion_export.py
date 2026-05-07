from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from .utils import now


def slugify_filename(value: str, suffix: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._") or "artifact"
    return stem if stem.endswith(suffix) else f"{stem}{suffix}"


def markdown_for_artifact(row: dict[str, Any], spec: dict[str, Any]) -> tuple[str, str]:
    kind = str(row.get("kind") or "")
    title = str(row.get("title") or "Artifact")
    caption = str(row.get("caption") or "")
    if kind == "file_draft":
        content = spec.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        filename = str(spec.get("filename") or "draft.md")
    elif kind == "chart":
        values = spec.get("values") if isinstance(spec, dict) else []
        narrative = spec.get("insight_narrative") if isinstance(spec.get("insight_narrative"), dict) else None
        lines = [f"# {title}", ""]
        if caption:
            lines.extend([caption, ""])
        if narrative:
            lines.extend(_narrative_markdown(narrative))
        lines.append("| Label | Value |")
        lines.append("| --- | ---: |")
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    lines.append(f"| {item.get('label', '')} | {item.get('value', '')} |")
        content = "\n".join(lines)
        filename = f"{title}.md"
    else:
        content = f"# {title}\n\n{caption}\n"
        table = table_payload_for_artifact(spec)
        if table:
            content += "\n" + markdown_table(table["columns"], table["rows"]) + "\n"
        filename = f"{title}.md"
    return content, slugify_filename(filename, ".md")


def _narrative_markdown(narrative: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    headline = str(narrative.get("headline") or "").strip()
    if headline:
        lines.extend(["## Insight", headline, ""])
    for heading, key in (("Meaning", "meaning"), ("So What", "so_what")):
        value = str(narrative.get(key) or "").strip()
        if value:
            lines.extend([f"## {heading}", value, ""])
    for heading, key in (
        ("Evidence", "evidence"),
        ("Recommended Actions", "recommended_actions"),
        ("Questions To Answer Next", "follow_up_questions"),
        ("Caveats", "caveats"),
    ):
        values = narrative.get(key)
        if not isinstance(values, list) or not values:
            continue
        lines.append(f"## {heading}")
        for item in values:
            if isinstance(item, dict):
                text = str(item.get("question") or item.get("label") or item.get("id") or "").strip()
            else:
                text = str(item).strip()
            if text:
                lines.append(f"- {text}")
        lines.append("")
    confidence = str(narrative.get("confidence") or "").strip()
    if confidence:
        lines.extend(["## Confidence", confidence, ""])
    return lines


def notion_import_bundle(row: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    markdown, filename = markdown_for_artifact(row, spec)
    table = table_payload_for_artifact(spec)
    return {
        "metadata": {
            "title": str(row.get("title") or "Artifact"),
            "kind": str(row.get("kind") or ""),
            "source_artifact_id": str(row.get("id") or ""),
            "source_chunk_ids": row.get("source_chunk_ids") or [],
            "exported_at": now(),
            "markdown_filename": filename,
            "datatable_filename": slugify_filename(str(row.get("title") or "artifact"), ".csv") if table else None,
        },
        "markdown": markdown,
        "datatable": table,
    }


def table_payload_for_artifact(spec: dict[str, Any]) -> dict[str, Any] | None:
    json_render_table = json_render_datatable(spec)
    if json_render_table:
        return json_render_table
    columns = spec.get("columns")
    rows = spec.get("rows")
    if isinstance(columns, list) and isinstance(rows, list):
        clean_columns = [str(column) for column in columns]
        clean_rows = [[_cell(cell) for cell in row] for row in rows if isinstance(row, list)]
        if clean_columns and clean_rows:
            return {"columns": clean_columns, "rows": clean_rows, "csv": rows_to_csv(clean_columns, clean_rows)}

    values = spec.get("values")
    if isinstance(values, list):
        clean_rows: list[list[str]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("name") or "").strip()
            value = item.get("value")
            if label:
                clean_rows.append([label, _cell(value)])
        if clean_rows:
            columns = ["Label", "Value"]
            return {"columns": columns, "rows": clean_rows, "csv": rows_to_csv(columns, clean_rows)}
    content = spec.get("content")
    if isinstance(content, str):
        markdown_table_payload = parse_markdown_table(content)
        if markdown_table_payload:
            return markdown_table_payload
    return None


def json_render_datatable(spec: dict[str, Any]) -> dict[str, Any] | None:
    elements = spec.get("elements")
    if not isinstance(elements, dict):
        return None
    for element in elements.values():
        if not isinstance(element, dict) or element.get("type") != "DataTable":
            continue
        props = element.get("props")
        if not isinstance(props, dict):
            continue
        columns = props.get("columns")
        rows = props.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list):
            continue
        clean_columns = [str(column) for column in columns]
        clean_rows = [[_cell(cell) for cell in row] for row in rows if isinstance(row, list)]
        if clean_columns and clean_rows:
            return {"columns": clean_columns, "rows": clean_rows, "csv": rows_to_csv(clean_columns, clean_rows)}
    return None


def parse_markdown_table(markdown: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in markdown.splitlines() if line.strip().startswith("|")]
    for index in range(len(lines) - 1):
        header = _markdown_row(lines[index])
        separator = _markdown_row(lines[index + 1])
        if not header or not separator or not all(set(cell.replace(":", "").strip()) <= {"-"} for cell in separator):
            continue
        rows: list[list[str]] = []
        for line in lines[index + 2 :]:
            row = _markdown_row(line)
            if len(row) != len(header):
                break
            rows.append(row)
        if rows:
            return {"columns": header, "rows": rows, "csv": rows_to_csv(header, rows)}
    return None


def _markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_table(columns: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        padded = row + [""] * max(0, len(columns) - len(row))
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in padded[: len(columns)]) + " |")
    return "\n".join(lines)


def rows_to_csv(columns: list[str], rows: list[list[str]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
