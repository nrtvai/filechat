from __future__ import annotations

import csv
import io
import json
import re
import textwrap
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



def pdf_for_artifact(row: dict[str, Any], spec: dict[str, Any]) -> tuple[bytes, str]:
    """Render an artifact's Markdown export as a simple downloadable PDF.

    This intentionally avoids arbitrary HTML rendering. It creates a deterministic
    text-first PDF from the same safe Markdown representation used by md export.
    """
    markdown, markdown_filename = markdown_for_artifact(row, spec)
    filename = slugify_filename(re.sub(r"\.md$", "", markdown_filename), ".pdf")
    return _simple_text_pdf(markdown), filename


def _simple_text_pdf(markdown: str) -> bytes:
    lines = _plain_pdf_lines(markdown)
    page_lines = 42
    pages = [lines[index : index + page_lines] for index in range(0, max(len(lines), 1), page_lines)] or [[]]
    objects: list[bytes] = []

    def add_object(payload: str | bytes) -> int:
        data = payload.encode("latin-1", errors="replace") if isinstance(payload, str) else payload
        objects.append(data)
        return len(objects)

    catalog_id = add_object(b"")
    pages_id = add_object(b"")
    font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    for page in pages:
        content_id = len(objects) + 2
        page_id = add_object(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)
        stream = _pdf_text_stream(page)
        add_object(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")

    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")

    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, payload in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode("latin-1"))
        output.write(payload)
        output.write(b"\nendobj\n")
    xref_offset = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
    )
    return output.getvalue()


def _plain_pdf_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = re.sub(r"^#{1,6}\s*", "", raw_line).strip()
        line = re.sub(r"^[-*]\s+", "- ", line)
        if not line:
            lines.append("")
            continue
        wrapped = textwrap.wrap(line, width=86, replace_whitespace=False, drop_whitespace=False)
        lines.extend(wrapped or [""])
    return lines or ["Artifact export"]


def _pdf_text_stream(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 11 Tf", "14 TL", "50 742 Td"]
    first = True
    for line in lines:
        if not first:
            commands.append("T*")
        first = False
        commands.append(f"({_pdf_literal(line)}) Tj")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", errors="replace")


def _pdf_literal(value: str) -> str:
    normalized = value.encode("latin-1", errors="replace").decode("latin-1")
    return normalized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


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
