from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .models import ArtifactKind


ALLOWED_ARTIFACT_KINDS: set[str] = {"mermaid", "chart", "table", "decision_cards", "comparison", "summary_panel", "file_draft"}
ALLOWED_JSON_RENDER_TYPES: set[str] = {
    "ArtifactCard",
    "Stack",
    "TextBlock",
    "Metric",
    "DataTable",
    "Quote",
    "Badge",
    "Divider",
    "SourceButton",
    "ActionButton",
    "MiniChart",
}


class JsonRenderElement(BaseModel):
    type: str
    props: dict[str, Any] = Field(default_factory=dict)
    children: list[str] = Field(default_factory=list)
    visible: Any | None = None

    @field_validator("type")
    @classmethod
    def type_must_be_allowlisted(cls, value: str) -> str:
        if value not in ALLOWED_JSON_RENDER_TYPES:
            raise ValueError(f"Unsupported artifact component: {value}")
        return value

    @model_validator(mode="after")
    def props_must_match_component(self) -> "JsonRenderElement":
        props = self.props
        if self.type == "TextBlock":
            _require_string(props, "text", self.type)
        elif self.type == "Metric":
            _require_string(props, "label", self.type)
            _require_string(props, "value", self.type)
        elif self.type == "DataTable":
            _require_string_list(props, "columns", self.type)
            rows = props.get("rows")
            if not isinstance(rows, list):
                raise ValueError("DataTable.rows must be an array")
            for row in rows:
                if not isinstance(row, list) or not all(isinstance(cell, str) for cell in row):
                    raise ValueError("DataTable.rows must be arrays of strings")
        elif self.type == "Quote":
            _require_string(props, "text", self.type)
        elif self.type == "Badge":
            _require_string(props, "label", self.type)
        elif self.type == "SourceButton":
            _require_string(props, "label", self.type)
            _require_string(props, "chunkId", self.type)
        elif self.type == "ActionButton":
            _require_string(props, "label", self.type)
        elif self.type == "MiniChart":
            values = props.get("values")
            if not isinstance(values, list) or not values:
                raise ValueError("MiniChart.values must be a non-empty array")
            for item in values:
                if not isinstance(item, dict) or not isinstance(item.get("label"), str) or not _finite_number(item.get("value")):
                    raise ValueError("MiniChart.values must contain label/value objects")
        return self


class JsonRenderSpec(BaseModel):
    root: str
    elements: dict[str, JsonRenderElement]

    @field_validator("elements")
    @classmethod
    def elements_must_not_be_empty(cls, value: dict[str, JsonRenderElement]) -> dict[str, JsonRenderElement]:
        if not value:
            raise ValueError("Artifact spec must contain at least one element")
        return value

    def model_post_init(self, __context: Any) -> None:
        if self.root not in self.elements:
            raise ValueError("Artifact spec root must exist in elements")
        for key, element in self.elements.items():
            for child in element.children:
                if child not in self.elements:
                    raise ValueError(f"Artifact element {key} references missing child {child}")


class RawArtifact(BaseModel):
    kind: ArtifactKind
    title: str = ""
    caption: str = ""
    display_mode: Literal["primary", "supporting"] = "primary"
    source_ids: list[int] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)
    diagram: str | None = None
    jsonRenderSpec: dict[str, Any] | None = None
    chart_type: Literal["bar", "line", "pie"] = "bar"
    values: list[dict[str, Any]] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[Any] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    filename: str = ""
    format: Literal["markdown", "json"] = "markdown"
    content: Any = ""

    @field_validator("kind", mode="before")
    @classmethod
    def kind_must_be_allowlisted(cls, value: str) -> str:
        if value not in ALLOWED_ARTIFACT_KINDS:
            raise ValueError(f"Unsupported artifact kind: {value}")
        return value


class ValidatedArtifact(BaseModel):
    kind: ArtifactKind
    title: str
    caption: str = ""
    display_mode: Literal["primary", "supporting"] = "primary"
    source_chunk_ids: list[str]
    spec: dict[str, Any]


class ArtifactValidationReport(BaseModel):
    artifacts: list[ValidatedArtifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _require_string(props: dict[str, Any], key: str, component: str) -> None:
    if not isinstance(props.get(key), str):
        raise ValueError(f"{component}.{key} must be a string")


def _require_string_list(props: dict[str, Any], key: str, component: str) -> None:
    value = props.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{component}.{key} must be an array of strings")


def _finite_number(value: Any) -> bool:
    return _coerce_number(value) is not None


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        cleaned = cleaned[:-1] if cleaned.endswith("%") else cleaned
        cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch in ".-")
        if cleaned in {"", ".", "-", "-."}:
            return None
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _chunk_ids_for(raw: RawArtifact, sources: list[dict[str, Any]], default_source_ids: list[int] | None = None) -> list[str]:
    by_source_id = {int(source["source_id"]): source for source in sources}
    allowed_chunk_ids = {str(source["chunk_id"]) for source in sources}
    chunk_ids = [str(chunk_id) for chunk_id in raw.source_chunk_ids if str(chunk_id) in allowed_chunk_ids]
    source_ids = raw.source_ids or (default_source_ids or [])
    for source_id in source_ids:
        source = by_source_id.get(source_id)
        if source:
            chunk_ids.append(str(source["chunk_id"]))
    for item in raw.values + raw.data:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        if isinstance(source_id, int) and source_id in by_source_id:
            chunk_ids.append(str(by_source_id[source_id]["chunk_id"]))
        source_chunk_id = item.get("source_chunk_id")
        if source_chunk_id and str(source_chunk_id) in allowed_chunk_ids:
            chunk_ids.append(str(source_chunk_id))
    return list(dict.fromkeys(chunk_ids))


def _chart_spec(raw: RawArtifact) -> dict[str, Any]:
    source_values = raw.values or raw.data
    values: list[dict[str, Any]] = []
    for item in source_values:
        if not isinstance(item, dict):
            raise ValueError("Chart values must be objects")
        label = str(item.get("label") or item.get("name") or item.get("category") or item.get("x") or item.get("answer") or "").strip()
        if not label:
            raise ValueError("Chart values require labels")
        number = _coerce_number(item.get("value") if item.get("value") is not None else item.get("count"))
        if number is None:
            raise ValueError("Chart values require finite numeric values")
        point: dict[str, Any] = {"label": label, "value": number}
        if item.get("source_id") is not None:
            point["source_id"] = item.get("source_id")
        if item.get("source_chunk_id") is not None:
            point["source_chunk_id"] = str(item.get("source_chunk_id"))
        values.append(point)
    if not values:
        raise ValueError("Chart artifact requires at least one value")
    return {
        "chart_type": raw.chart_type,
        "values": values,
        "x_label": raw.x_label.strip() or "Category",
        "y_label": raw.y_label.strip() or "Value",
    }


def _draft_spec(raw: RawArtifact) -> dict[str, Any]:
    content = raw.content
    if raw.format == "json" and not isinstance(content, str):
        content = content
    elif not isinstance(content, str):
        raise ValueError("Draft content must be text")
    if raw.format == "markdown" and not str(content).strip():
        raise ValueError("Markdown draft requires content")
    filename = raw.filename.strip() or ("draft.md" if raw.format == "markdown" else "draft.json")
    if "/" in filename or "\\" in filename:
        raise ValueError("Draft filename must not contain path separators")
    return {"filename": filename, "format": raw.format, "content": content}


def _normalize_json_render_spec(spec: dict[str, Any]) -> dict[str, Any]:
    elements: dict[str, Any] = {}
    counter = 0

    def next_id(prefix: str = "node") -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}_{counter}"

    def normalize_element(element: Any, element_id: str) -> str:
        if isinstance(element, str):
            return element
        if not isinstance(element, dict):
            raise ValueError("jsonRenderSpec elements must be objects")
        element_type = element.get("type")
        if not isinstance(element_type, str):
            raise ValueError("jsonRenderSpec element requires type")
        children: list[str] = []
        for child in element.get("children") or []:
            if isinstance(child, str):
                children.append(child)
            elif isinstance(child, dict):
                child_id = next_id("child")
                children.append(normalize_element(child, child_id))
            else:
                raise ValueError("jsonRenderSpec children must be ids or objects")
        elements[element_id] = {
            "type": element_type,
            "props": element.get("props") if isinstance(element.get("props"), dict) else {},
            "children": children,
        }
        if "visible" in element:
            elements[element_id]["visible"] = element["visible"]
        return element_id

    root = spec.get("root")
    raw_elements = spec.get("elements") if isinstance(spec.get("elements"), dict) else {}
    if isinstance(root, dict):
        normalized_root = normalize_element(root, "root")
    elif isinstance(root, str):
        normalized_root = root
    elif "type" in spec:
        normalized_root = normalize_element(spec, "root")
    else:
        raise ValueError("Artifact spec root must be a string or element object")

    for key, element in raw_elements.items():
        normalize_element(element, str(key))
    return {"root": normalized_root, "elements": elements}


def _table_spec(raw: RawArtifact) -> dict[str, Any]:
    if raw.jsonRenderSpec is not None:
        return JsonRenderSpec.model_validate(_normalize_json_render_spec(raw.jsonRenderSpec)).model_dump()
    columns = [str(column).strip() for column in raw.columns if str(column).strip()]
    if not columns:
        raise ValueError("Table artifact requires columns")
    rows: list[list[str]] = []
    for row in raw.rows:
        if isinstance(row, dict):
            rows.append([str(row.get(column, "")) for column in columns])
        elif isinstance(row, list):
            rows.append([str(cell) for cell in row[: len(columns)]])
        else:
            raise ValueError("Table rows must be arrays or objects")
    if not rows:
        raise ValueError("Table artifact requires rows")
    return {
        "root": "card",
        "elements": {
            "card": {
                "type": "ArtifactCard",
                "props": {"title": raw.title or "Table", "caption": raw.caption},
                "children": ["table"],
            },
            "table": {"type": "DataTable", "props": {"columns": columns, "rows": rows[:50]}, "children": []},
        },
    }


def _summary_panel_spec(raw: RawArtifact) -> dict[str, Any]:
    if raw.jsonRenderSpec is not None:
        return JsonRenderSpec.model_validate(_normalize_json_render_spec(raw.jsonRenderSpec)).model_dump()
    if not raw.sections:
        raise ValueError("Summary panel requires sections")
    elements: dict[str, Any] = {
        "card": {
            "type": "ArtifactCard",
            "props": {"title": raw.title or "Summary", "caption": raw.caption},
            "children": [],
        }
    }
    for index, section in enumerate(raw.sections[:8], start=1):
        heading = str(section.get("heading") or section.get("title") or f"Section {index}").strip()
        body = str(section.get("body") or section.get("text") or "").strip()
        if not body:
            continue
        element_id = f"section_{index}"
        elements[element_id] = {"type": "TextBlock", "props": {"text": f"{heading}: {body}"}, "children": []}
        elements["card"]["children"].append(element_id)
    if not elements["card"]["children"]:
        raise ValueError("Summary panel sections require body text")
    return {"root": "card", "elements": elements}


def validate_artifacts(raw_artifacts: list[Any], sources: list[dict[str, Any]], default_source_ids: list[int] | None = None) -> list[ValidatedArtifact]:
    return validate_artifacts_with_report(raw_artifacts, sources, default_source_ids=default_source_ids).artifacts


def validate_artifacts_with_report(raw_artifacts: list[Any], sources: list[dict[str, Any]], default_source_ids: list[int] | None = None) -> ArtifactValidationReport:
    validated: list[ValidatedArtifact] = []
    warnings: list[str] = []

    for index, item in enumerate(raw_artifacts, start=1):
        try:
            raw = RawArtifact.model_validate(item)
            chunk_ids = _chunk_ids_for(raw, sources, default_source_ids)
            if not chunk_ids:
                raise ValueError("Artifact must cite at least one retrieved source chunk")

            title = raw.title.strip() or raw.kind.replace("_", " ").title()
            caption = raw.caption.strip()
            if raw.kind == "mermaid":
                diagram = (raw.diagram or "").strip()
                if not diagram:
                    raise ValueError("Mermaid artifact requires a diagram")
                spec: dict[str, Any] = {"diagram": diagram}
            elif raw.kind == "chart":
                spec = _chart_spec(raw)
            elif raw.kind == "file_draft":
                spec = _draft_spec(raw)
            elif raw.kind == "table":
                spec = _table_spec(raw)
            elif raw.kind == "summary_panel":
                spec = _summary_panel_spec(raw)
            else:
                if raw.jsonRenderSpec is None:
                    raise ValueError(f"{raw.kind} artifact requires jsonRenderSpec")
                spec_model = JsonRenderSpec.model_validate(_normalize_json_render_spec(raw.jsonRenderSpec))
                spec = spec_model.model_dump()

            validated.append(
                ValidatedArtifact(
                    kind=raw.kind,
                    title=title,
                    caption=caption,
                    display_mode=raw.display_mode,
                    source_chunk_ids=chunk_ids,
                    spec=spec,
                )
            )
        except (TypeError, ValueError, ValidationError) as exc:
            warnings.append(f"Artifact {index} was not persisted: {exc}")

    return ArtifactValidationReport(artifacts=validated, warnings=warnings)


def safe_validate_artifacts(raw_artifacts: list[Any], sources: list[dict[str, Any]]) -> list[ValidatedArtifact]:
    return validate_artifacts_with_report(raw_artifacts, sources).artifacts
