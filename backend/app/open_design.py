from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from .notion_export import slugify_filename

SUPPORTED_MATERIAL_TYPES = {"design_brief", "docs_page", "report", "one_pager", "material_pack"}
_METADATA_KEYS = {"material_type", "skill_name", "audience", "brand", "tone", "purpose"}

NEUTRAL_DESIGN_MD = """# FileChat Neutral Design System

## 1. Purpose
Ground source-backed FileChat design documents and materials in a portable Open Design-compatible bundle.

## 2. Audience
Use clear, professional language for the target audience named in metadata, or a general business/product audience.

## 3. Brand Voice
Neutral, concise, evidence-led, and safe for reuse outside FileChat.

## 4. Content Principles
Preserve the supplied `content.md` as the primary artifact. Do not invent facts beyond cited source material.

## 5. Information Architecture
Lead with context, key findings or recommendations, supporting evidence, and next steps.

## 6. Visual Style
Use simple typography, readable spacing, and accessible contrast if the material is later rendered by another tool.

## 7. Components
Prefer headings, bullets, tables, callouts, and source notes. Avoid executable or remote runtime dependencies.

## 8. Accessibility
Keep language plain, structure headings hierarchically, and include text alternatives for any future visuals.

## 9. Delivery Notes
This bundle is compatibility-oriented only: FileChat exports Markdown and metadata, not the Open Design runtime or arbitrary HTML preview.
"""


def _safe_text(value: Any, *, max_length: int = 120) -> str:
    text = re.sub(r"<[^>]*>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]


def _safe_slug(value: Any, fallback: str) -> str:
    text = _safe_text(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or fallback


def normalize_open_design_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        metadata = {}
    normalized: dict[str, Any] = {}
    material_type = _safe_slug(metadata.get("material_type"), "docs_page").replace("-", "_")
    normalized["material_type"] = material_type if material_type in SUPPORTED_MATERIAL_TYPES else "docs_page"
    skill_name_text = _safe_text(metadata.get("skill_name"), max_length=120)
    normalized["skill_name"] = _safe_slug(skill_name_text, "filechat-design-material")
    if skill_name_text:
        normalized["skill_display_name"] = skill_name_text
    for key in sorted(_METADATA_KEYS - {"material_type", "skill_name"}):
        if key in metadata:
            value = _safe_text(metadata.get(key), max_length=240)
            if value:
                normalized[key] = value
    return normalized


def is_open_design_eligible(row: dict[str, Any], spec: dict[str, Any]) -> bool:
    return str(row.get("kind") or "") == "file_draft" and isinstance(spec.get("open_design"), dict)


def _skill_md(title: str, metadata: dict[str, Any]) -> str:
    skill_name = metadata.get("skill_display_name") or metadata.get("skill_name") or _safe_slug(title, "filechat-design-material")
    description = f"Open Design-compatible FileChat export for {metadata['material_type'].replace('_', ' ')} materials."
    return (
        "---\n"
        f"name: {skill_name}\n"
        f"description: {description}\n"
        "od:\n"
        "  compatibility: filechat-open-design-export-v1\n"
        f"  material_type: {metadata['material_type']}\n"
        "---\n\n"
        "# FileChat Open Design Export Skill\n\n"
        "Use `content.md` as the source-grounded draft and `DESIGN.md` as the neutral design system. "
        "This bundle intentionally contains no executable runtime or arbitrary HTML preview.\n"
    )


def open_design_bundle_for_artifact(row: dict[str, Any], spec: dict[str, Any]) -> tuple[bytes, str]:
    if not is_open_design_eligible(row, spec):
        raise ValueError("Artifact is not eligible for Open Design ZIP export")
    metadata = normalize_open_design_metadata(spec.get("open_design"))
    title = _safe_text(row.get("title"), max_length=160) or "FileChat material"
    source_chunk_ids = row.get("source_chunk_ids") if isinstance(row.get("source_chunk_ids"), list) else []
    content = spec.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)
    metadata_payload = {
        "open_design_compatible": True,
        "compatibility_version": "filechat-open-design-export-v1",
        "source": "filechat",
        "source_artifact_id": str(row.get("id") or ""),
        "title": title,
        "caption": _safe_text(row.get("caption"), max_length=240),
        "source_chunk_ids": [str(item) for item in source_chunk_ids],
        **metadata,
    }

    files = [
        ("SKILL.md", _skill_md(title, metadata).encode("utf-8")),
        ("DESIGN.md", NEUTRAL_DESIGN_MD.encode("utf-8")),
        ("content.md", content.encode("utf-8")),
        ("metadata.json", json.dumps(metadata_payload, ensure_ascii=False, indent=2).encode("utf-8")),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    filename = slugify_filename(_safe_slug(f"{title}-open-design", "filechat-open-design"), ".zip")
    return buffer.getvalue(), filename
