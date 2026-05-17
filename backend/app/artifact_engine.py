from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import ValidatedArtifact, validate_artifacts_with_report
from .openrouter import ChatResult
from .providers import Provider, provider_registry
from .spreadsheet_mode import extract_table_text_from_spreadsheet_summary
from .usage import UsageInfo


DISCOVERY_PATTERNS = (
    "what charts",
    "what chart",
    "what docs",
    "what documents",
    "what can you make",
    "available outputs",
    "available artifacts",
    "can you make with this",
)
ARTIFACT_KINDS = {"chart", "table", "summary_panel", "file_draft", "comparison", "mermaid"}
MAX_REPAIR_ATTEMPTS = 2


class ArtifactEngineFailure(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass
class ArtifactEngineInput:
    run_id: str
    session_id: str
    question: str
    task_contract: dict[str, Any]
    sources: list[dict[str, Any]]
    file_texts: list[dict[str, Any]]
    unavailable: list[str]
    history: list[dict[str, str]]
    prompt_context: dict[str, Any]
    settings: dict[str, Any]
    follow_up_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactEngineResult:
    answer: str
    cited_source_ids: list[int]
    artifacts: list[ValidatedArtifact]
    raw_artifacts: list[dict[str, Any]]
    source_profile: dict[str, Any]
    artifact_plan: dict[str, Any]
    build_result: dict[str, Any]
    warnings: list[str]
    model: str
    usage: UsageInfo
    repair_attempts: list[dict[str, Any]] = field(default_factory=list)
    workspace_items: list[dict[str, Any]] = field(default_factory=list)
    review_reports: list[dict[str, Any]] = field(default_factory=list)


def is_artifact_discovery_question(question: str) -> bool:
    normalized = re.sub(r"\s+", " ", question.lower()).strip()
    return any(pattern in normalized for pattern in DISCOVERY_PATTERNS)


def selected_artifact_options(follow_up_context: dict[str, Any]) -> list[dict[str, Any]]:
    raw = follow_up_context.get("selected_artifact_options") if isinstance(follow_up_context, dict) else []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or option_id).strip()
        artifact_kind = str(item.get("artifact_kind") or "summary_panel").strip()
        if not option_id or not label or artifact_kind not in ARTIFACT_KINDS:
            continue
        option: dict[str, Any] = {
            "id": option_id,
            "label": label,
            "description": str(item.get("description") or ""),
            "artifact_kind": artifact_kind,
        }
        chart_type = str(item.get("chart_type") or "").strip()
        if chart_type:
            option["chart_type"] = chart_type
        produce_payload = item.get("produce_payload")
        if isinstance(produce_payload, dict):
            option["produce_payload"] = {
                key: value
                for key, value in produce_payload.items()
                if key in {"artifact_kind", "chart_type", "title", "label", "description", "source_columns", "source_facts"}
            }
        out.append(option)
    return out


def profile_sources(file_texts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any]:
    source_refs = [
        {
            "source_id": source.get("source_id"),
            "file_id": source.get("file_id"),
            "file_name": source.get("file_name"),
            "location": source.get("location"),
            "chunk_id": source.get("chunk_id"),
            "excerpt": _compact_text(str(source.get("excerpt") or source.get("content") or ""), 360),
        }
        for source in sources[:20]
    ]
    tables = []
    texts = []
    for item in file_texts:
        file_id = str(item.get("file_id") or "")
        file_name = str(item.get("file_name") or "source")
        text = str(item.get("text") or "")
        table = parse_table(text, file_id=file_id, file_name=file_name)
        source_ids = [int(source["source_id"]) for source in sources if str(source.get("file_id") or "") == file_id and source.get("source_id") is not None]
        chunk_ids = [str(source.get("chunk_id")) for source in sources if str(source.get("file_id") or "") == file_id and source.get("chunk_id")]
        if table:
            tables.append(_profile_table(table, source_ids=source_ids, chunk_ids=chunk_ids))
        else:
            texts.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "character_count": len(text),
                    "line_count": len(text.splitlines()),
                    "source_ids": source_ids[:8],
                    "source_chunk_ids": chunk_ids[:8],
                    "samples": [_compact_text(line, 220) for line in text.splitlines() if line.strip()][:6],
                }
            )
    return {
        "version": 1,
        "summary": _profile_summary(tables, texts, source_refs),
        "sources": source_refs,
        "tables": tables,
        "texts": texts,
        "available_operations": [
            "group",
            "sum",
            "count",
            "average",
            "min",
            "max",
            "top_categories",
            "time_bucket",
        ],
        "diagnostics": {
            "source_count": len(sources),
            "file_count": len(file_texts),
            "table_count": len(tables),
            "text_count": len(texts),
        },
    }


async def build_retrieval_artifacts(request: ArtifactEngineInput) -> ArtifactEngineResult:
    return await ArtifactEngine(provider_registry().active()).run(request)


class ArtifactEngine:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    async def run(self, request: ArtifactEngineInput) -> ArtifactEngineResult:
        source_profile = profile_sources(request.file_texts, request.sources)
        selected_options = selected_artifact_options(request.follow_up_context)
        discovery_only = is_artifact_discovery_question(request.question) and not selected_options
        if selected_options:
            artifact_plan = _plan_from_selected_options(selected_options, request.sources)
        else:
            try:
                artifact_plan = await self.provider.plan_artifacts(
                    model=str(request.settings.get("orchestrator_model") or request.settings.get("chat_model") or ""),
                    question=request.question,
                    task_contract=request.task_contract,
                    source_profile=source_profile,
                    prompt_context=request.prompt_context,
                    selected_options=selected_options,
                    discovery_only=discovery_only,
                    reasoning_effort=str(request.settings.get("reasoning_effort") or "none"),
                )
            except Exception as exc:
                raise ArtifactEngineFailure(
                    "FileChat could not plan safe artifacts from the model response.",
                    _provider_failure_diagnostics(
                        stage="plan",
                        error=exc,
                        source_profile=source_profile,
                        artifact_plan={},
                    ),
                ) from exc
        artifact_plan = _normalize_plan(artifact_plan, discovery_only=discovery_only, selected_options=selected_options)
        planner_attempts = artifact_plan.get("_planner_attempts", []) if isinstance(artifact_plan.get("_planner_attempts"), list) else []
        planner_workspace_items = (
            [{"path": "/review/artifact-planner-attempts.json", "kind": "review", "content": {"attempts": planner_attempts}}]
            if planner_attempts
            else []
        )
        if discovery_only:
            raw_artifacts = [_decision_cards_artifact(artifact_plan, request.sources)]
            build_result = {
                "answer": _discovery_answer(artifact_plan),
                "cited_source_ids": _plan_citations(artifact_plan, request.sources),
                "artifacts": raw_artifacts,
                "unresolved_issues": [],
            }
            report = validate_artifacts_with_report(raw_artifacts, request.sources, default_source_ids=build_result["cited_source_ids"])
            if report.warnings or not report.artifacts:
                raise ArtifactEngineFailure(
                    "FileChat could not generate safe artifacts after validation.",
                    {"warnings": report.warnings, "artifact_plan": artifact_plan},
                )
            return ArtifactEngineResult(
                answer=str(build_result["answer"]),
                cited_source_ids=list(build_result["cited_source_ids"]),
                artifacts=report.artifacts,
                raw_artifacts=raw_artifacts,
                source_profile=source_profile,
                artifact_plan=artifact_plan,
                build_result=build_result,
                warnings=report.warnings,
                model="artifact-planner",
                usage=UsageInfo(),
                workspace_items=[
                    {"path": "/analysis/source-profile.json", "kind": "analysis", "content": source_profile},
                    {"path": "/plan/artifact-plan.json", "kind": "planning", "content": artifact_plan},
                    {"path": "/build/artifact-build.json", "kind": "build", "content": _public_build(build_result)},
                    *planner_workspace_items,
                ],
            )

        writing_model = str(request.settings.get("writing_model") or request.settings.get("chat_model") or "")
        effective_planner_model = str(artifact_plan.get("_effective_planner_model") or "").strip()
        failed_planner_models = {
            str(attempt.get("model") or "")
            for attempt in artifact_plan.get("_planner_attempts", [])
            if isinstance(attempt, dict) and attempt.get("status") == "failed"
        }
        if effective_planner_model and writing_model in failed_planner_models:
            writing_model = effective_planner_model
        try:
            build_chat = await self.provider.build_artifacts(
                model=writing_model,
                question=request.question,
                artifact_plan=artifact_plan,
                source_profile=source_profile,
                sources=request.sources,
                prompt_context=request.prompt_context,
                reasoning_effort=str(request.settings.get("reasoning_effort") or "none"),
            )
        except Exception as exc:
            raise ArtifactEngineFailure(
                "FileChat could not build safe artifacts from the model response.",
                _provider_failure_diagnostics(
                    stage="build",
                    error=exc,
                    source_profile=source_profile,
                    artifact_plan=artifact_plan,
                ),
            ) from exc
        result = _chat_to_build_result(build_chat)
        model = build_chat.model if isinstance(build_chat, ChatResult) else writing_model
        usage = build_chat.usage if isinstance(build_chat, ChatResult) else UsageInfo()
        return await self._validate_review_repair(
            request=request,
            source_profile=source_profile,
            artifact_plan=artifact_plan,
            build_result=result,
            model=model,
            usage=usage,
        )

    async def _validate_review_repair(
        self,
        *,
        request: ArtifactEngineInput,
        source_profile: dict[str, Any],
        artifact_plan: dict[str, Any],
        build_result: dict[str, Any],
        model: str,
        usage: UsageInfo,
    ) -> ArtifactEngineResult:
        repair_attempts: list[dict[str, Any]] = []
        review_reports: list[dict[str, Any]] = []
        workspace_items = [
            {"path": "/analysis/source-profile.json", "kind": "analysis", "content": source_profile},
            {"path": "/plan/artifact-plan.json", "kind": "planning", "content": artifact_plan},
        ]
        planner_attempts = artifact_plan.get("_planner_attempts", []) if isinstance(artifact_plan.get("_planner_attempts"), list) else []
        if planner_attempts:
            workspace_items.append({"path": "/review/artifact-planner-attempts.json", "kind": "review", "content": {"attempts": planner_attempts}})
        current = build_result
        all_warnings: list[str] = []
        for attempt in range(0, MAX_REPAIR_ATTEMPTS + 1):
            report = validate_artifacts_with_report(current.get("artifacts", []), request.sources, default_source_ids=current.get("cited_source_ids", []))
            warnings = [*report.warnings, *_contract_warnings(artifact_plan, current, report.artifacts)]
            all_warnings.extend(warnings)
            workspace_items.append(
                {
                    "path": "/build/artifact-build.json" if attempt == 0 else f"/review/repair-attempt-{attempt}.json",
                    "kind": "build" if attempt == 0 else "review",
                    "content": {
                        "attempt": attempt,
                        "answer": str(current.get("answer") or ""),
                        "artifact_count": len(current.get("artifacts", [])) if isinstance(current.get("artifacts"), list) else 0,
                        "validated_artifact_count": len(report.artifacts),
                        "warnings": warnings,
                        "unresolved_issues": current.get("unresolved_issues", []),
                    },
                }
            )
            red_team = await self._red_team(
                request=request,
                source_profile=source_profile,
                artifact_plan=artifact_plan,
                build_result=current,
                artifacts=report.artifacts,
                validation_warnings=warnings,
            )
            if red_team:
                review_reports.append(red_team)
                if attempt == 0:
                    workspace_items.append({"path": "/review/red-team.json", "kind": "review", "content": red_team})
            needs_repair = bool(warnings) or _review_failed(red_team)
            if not needs_repair:
                cited = [source_id for source_id in current.get("cited_source_ids", []) if isinstance(source_id, int)]
                if report.artifacts and not cited and request.sources:
                    cited = [int(request.sources[0]["source_id"])]
                return ArtifactEngineResult(
                    answer=str(current.get("answer") or "").strip(),
                    cited_source_ids=cited,
                    artifacts=report.artifacts,
                    raw_artifacts=current.get("artifacts", []) if isinstance(current.get("artifacts"), list) else [],
                    source_profile=source_profile,
                    artifact_plan=artifact_plan,
                    build_result=current,
                    warnings=list(dict.fromkeys(warnings)),
                    model=model,
                    usage=usage,
                    repair_attempts=repair_attempts,
                    workspace_items=workspace_items,
                    review_reports=review_reports,
                )
            if attempt >= MAX_REPAIR_ATTEMPTS:
                break
            repair_record = {
                "strategy": "artifact_engine_repair",
                "attempt": attempt + 1,
                "warnings": warnings,
                "red_team": red_team or {},
                "result": "requested",
            }
            repaired = await self.provider.repair_artifacts(
                model=str(request.settings.get("repair_model") or request.settings.get("writing_model") or ""),
                question=request.question,
                artifact_plan=artifact_plan,
                source_profile=source_profile,
                sources=request.sources,
                current_build=current,
                validation_warnings=warnings,
                red_team_report=red_team or {},
                prompt_context=request.prompt_context,
                repair_attempt=attempt + 1,
                reasoning_effort=str(request.settings.get("reasoning_effort") or "none"),
            )
            current = _chat_to_build_result(repaired)
            repair_record["result"] = "received"
            repair_attempts.append(repair_record)
        diagnostics = {
            "artifact_plan": artifact_plan,
            "last_build": _public_build(current),
            "warnings": list(dict.fromkeys(all_warnings)),
            "review_reports": review_reports,
            "repair_attempts": repair_attempts,
            "workspace_items": workspace_items,
        }
        raise ArtifactEngineFailure("FileChat could not generate safe artifacts after 2 repair attempts.", diagnostics)

    async def _red_team(
        self,
        *,
        request: ArtifactEngineInput,
        source_profile: dict[str, Any],
        artifact_plan: dict[str, Any],
        build_result: dict[str, Any],
        artifacts: list[ValidatedArtifact],
        validation_warnings: list[str],
    ) -> dict[str, Any] | None:
        if not artifacts and not validation_warnings:
            return None
        try:
            return await self.provider.review_phase(
                model=str(request.settings.get("repair_model") or request.settings.get("writing_model") or ""),
                phase="red_team",
                phase_goal=(
                    "Review AI-built artifacts against the source profile, citations, and selected artifact plan. "
                    "Block only for material artifact validity, citation, grounding, or contract failures. "
                    "Treat missing next actions, optional caveats, and extra follow-up suggestions as low severity unless the user explicitly requested them."
                ),
                task_contract=request.task_contract,
                evidence_packet={"source_profile": source_profile, "artifact_plan": artifact_plan, "validation_warnings": validation_warnings},
                source_refs=source_profile.get("sources", []),
                artifact_specs=[
                    {"kind": artifact.kind, "title": artifact.title, "spec": artifact.spec}
                    for artifact in artifacts
                ],
                answer_draft=str(build_result.get("answer") or ""),
                prior_checker_reports=[],
            )
        except Exception as exc:
            return {
                "phase": "red_team",
                "passed": True,
                "severity": "low",
                "findings": [f"Red-team review unavailable: {exc}"],
                "required_fixes": [],
                "suggested_followups": [],
                "confidence": "low",
            }


def parse_table(text: str, *, file_id: str, file_name: str) -> dict[str, Any] | None:
    cleaned = extract_table_text_from_spreadsheet_summary(text).lstrip("\ufeff").strip()
    if not cleaned or ("," not in cleaned and "\t" not in cleaned and ";" not in cleaned):
        return None
    delimiter = _sniff_delimiter(cleaned, file_name)
    lines = [line for line in cleaned.splitlines() if line.strip()]
    if len(lines) >= 2:
        first_cells = [cell.strip() for cell in lines[0].split(delimiter)]
        second_cells = [cell.strip() for cell in lines[1].split(delimiter)]
        if len(first_cells) == 1 and len(second_cells) >= 2:
            columns = ["Label", "Value", *[f"Value {index}" for index in range(2, len(second_cells))]]
            rows = []
            for line in lines[1:501]:
                cells = [cell.strip() for cell in line.split(delimiter)]
                if len(cells) < 2:
                    continue
                row = {column: cells[index] if index < len(cells) else "" for index, column in enumerate(columns)}
                if any(row.values()):
                    rows.append(row)
            if rows:
                return {"file_id": file_id, "file_name": file_name, "columns": columns, "rows": rows, "delimiter": delimiter}
    reader = csv.DictReader(io.StringIO(cleaned), delimiter=delimiter)
    if not reader.fieldnames:
        return None
    columns = [str(column or "").strip() for column in reader.fieldnames]
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {column: str(raw.get(column) or "").strip() for column in columns}
        if any(row.values()):
            rows.append(row)
        if len(rows) >= 500:
            break
    if not rows:
        return None
    return {"file_id": file_id, "file_name": file_name, "columns": columns, "rows": rows, "delimiter": delimiter}


def _profile_table(table: dict[str, Any], *, source_ids: list[int], chunk_ids: list[str]) -> dict[str, Any]:
    rows = table["rows"]
    columns = [_profile_column(rows, column) for column in table["columns"]]
    return {
        "file_id": table["file_id"],
        "file_name": table["file_name"],
        "row_count": len(rows),
        "column_count": len(table["columns"]),
        "columns": columns,
        "source_ids": source_ids[:8],
        "source_chunk_ids": chunk_ids[:8],
        "sample_rows": [
            {column: _compact_text(str(row.get(column, "")), 80) for column in table["columns"][:8]}
            for row in rows[:5]
        ],
    }


def _profile_column(rows: list[dict[str, str]], column: str) -> dict[str, Any]:
    values = [str(row.get(column) or "").strip() for row in rows]
    non_empty = [value for value in values if value]
    numeric = [_number(value) for value in non_empty]
    numeric_values = [value for value in numeric if value is not None]
    date_count = sum(1 for value in non_empty if _looks_date(value))
    unique_values = list(dict.fromkeys(non_empty))
    role = "empty"
    if non_empty:
        numeric_ratio = len(numeric_values) / len(non_empty)
        date_ratio = date_count / len(non_empty)
        unique_ratio = len(set(non_empty)) / len(non_empty)
        if date_ratio >= 0.65:
            role = "date_like"
        elif numeric_ratio >= 0.75 and not _looks_identifier_column(column, non_empty):
            role = "numeric"
        elif unique_ratio > 0.9 and _avg_len(non_empty) <= 12:
            role = "identifier_like"
        elif len(set(non_empty)) <= max(3, min(20, len(non_empty) // 2)) and _avg_len(non_empty) <= 80:
            role = "categorical"
        else:
            role = "text"
    out: dict[str, Any] = {
        "name": column,
        "role": role,
        "non_empty": len(non_empty),
        "unique": len(set(non_empty)),
        "sample_values": [_compact_text(value, 80) for value in unique_values[:6]],
    }
    if numeric_values:
        out["numeric"] = {
            "min": min(numeric_values),
            "max": max(numeric_values),
            "average": round(sum(numeric_values) / len(numeric_values), 4),
        }
    if role == "categorical":
        out["top_values"] = _top_values(non_empty)
    return out


def _looks_identifier_column(column: str, values: list[str]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
    if normalized in {"id", "uuid", "guid"} or normalized.endswith("_id"):
        return True
    return bool(values) and _avg_len(values) >= 8 and len(set(values)) == len(values)


def _decision_cards_artifact(plan: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids = _plan_citations(plan, sources)
    source = sources[0] if sources else {}
    options = []
    elements: dict[str, Any] = {
        "card": {
            "type": "ArtifactCard",
            "props": {"title": "Available Charts And Docs", "caption": str(plan.get("rationale") or "Artifact options from the source profile.")},
            "children": ["intro", "options"],
        },
        "intro": {
            "type": "TextBlock",
            "props": {"text": "Choose one or more source-grounded artifacts to produce.", "tone": "muted"},
            "children": [],
        },
        "options": {"type": "Stack", "props": {"gap": "sm"}, "children": []},
    }
    for index, item in enumerate(plan.get("artifacts", [])[:8], start=1):
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("id") or f"option_{index}").strip()
        title = str(item.get("title") or option_id).strip()
        artifact_kind = str(item.get("artifact_kind") or "summary_panel").strip()
        if artifact_kind not in ARTIFACT_KINDS:
            artifact_kind = "summary_panel"
        chart_type = str(item.get("chart_type") or "").strip()
        description = str(item.get("description") or item.get("rationale") or "Source-grounded artifact option.").strip()
        produce_payload: dict[str, Any] = {
            "artifact_kind": artifact_kind,
            "title": title,
            "description": description,
            "source_columns": [str(value) for value in item.get("source_columns", []) if str(value).strip()]
            if isinstance(item.get("source_columns"), list)
            else [],
        }
        if chart_type:
            produce_payload["chart_type"] = chart_type
        option = {
            "id": option_id,
            "label": title,
            "description": description,
            "artifact_kind": artifact_kind,
            "produce_payload": produce_payload,
        }
        if chart_type:
            option["chart_type"] = chart_type
        options.append(option)
        row_id = f"option_{index}"
        badge_id = f"badge_{index}"
        title_id = f"title_{index}"
        desc_id = f"description_{index}"
        elements["options"]["children"].append(row_id)
        elements[row_id] = {"type": "Stack", "props": {"gap": "xs"}, "children": [badge_id, title_id, desc_id]}
        badge = f"{chart_type.title()} Chart" if chart_type else artifact_kind.replace("_", " ").title()
        elements[badge_id] = {"type": "Badge", "props": {"label": badge, "tone": "accent"}, "children": []}
        elements[title_id] = {"type": "TextBlock", "props": {"text": title, "tone": "strong"}, "children": []}
        elements[desc_id] = {"type": "TextBlock", "props": {"text": description, "tone": "muted"}, "children": []}
    chunk_id = str(source.get("chunk_id") or "")
    if chunk_id:
        elements["card"]["children"].append("source")
        elements["source"] = {"type": "SourceButton", "props": {"label": "Open source", "chunkId": chunk_id}, "children": []}
    return {
        "kind": "decision_cards",
        "title": "Available Charts And Docs",
        "caption": str(plan.get("rationale") or "Artifact options from the source profile."),
        "display_mode": "primary",
        "source_ids": source_ids,
        "source_chunk_ids": [chunk_id] if chunk_id else [],
        "decision_options": options,
        "jsonRenderSpec": {"root": "card", "elements": elements},
    }


def _normalize_plan(plan: dict[str, Any], *, discovery_only: bool, selected_options: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        plan = {}
    artifacts = plan.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    normalized_artifacts: list[dict[str, Any]] = []
    for index, item in enumerate(artifacts, start=1):
        if not isinstance(item, dict):
            continue
        artifact_kind = str(item.get("artifact_kind") or item.get("kind") or "summary_panel").strip()
        if artifact_kind not in ARTIFACT_KINDS:
            artifact_kind = "summary_panel"
        option_id = str(item.get("id") or f"artifact_{index}").strip()
        normalized_artifacts.append({**item, "id": option_id, "artifact_kind": artifact_kind})
    if selected_options:
        by_id = {item["id"]: item for item in selected_options}
        ordered: list[dict[str, Any]] = []
        for option in selected_options:
            planned = next((item for item in normalized_artifacts if item["id"] == option["id"]), {})
            ordered.append({**planned, **option, "title": planned.get("title") or option["label"]})
        normalized_artifacts = ordered
    normalized = {
        "mode": "discovery" if discovery_only else str(plan.get("mode") or ("selected" if selected_options else "create")),
        "artifacts": normalized_artifacts,
        "rationale": str(plan.get("rationale") or ""),
        "required_citations": [int(value) for value in plan.get("required_citations", []) if str(value).isdigit()]
        if isinstance(plan.get("required_citations"), list)
        else [],
        "caveats": [str(value) for value in plan.get("caveats", []) if str(value).strip()]
        if isinstance(plan.get("caveats"), list)
        else [],
        "acceptance_criteria": [str(value) for value in plan.get("acceptance_criteria", []) if str(value).strip()]
        if isinstance(plan.get("acceptance_criteria"), list)
        else [],
    }
    if isinstance(plan.get("_planner_attempts"), list):
        normalized["_planner_attempts"] = plan["_planner_attempts"]
    if str(plan.get("_effective_planner_model") or "").strip():
        normalized["_effective_planner_model"] = str(plan["_effective_planner_model"])
    return normalized


def _plan_from_selected_options(selected_options: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids = [int(source["source_id"]) for source in sources if source.get("source_id") is not None]
    artifacts: list[dict[str, Any]] = []
    for option in selected_options:
        if not isinstance(option, dict):
            continue
        payload = option.get("produce_payload") if isinstance(option.get("produce_payload"), dict) else {}
        artifacts.append(
            {
                "id": str(option.get("id") or "").strip(),
                "artifact_kind": str(option.get("artifact_kind") or payload.get("artifact_kind") or "summary_panel").strip(),
                "chart_type": str(option.get("chart_type") or payload.get("chart_type") or "").strip(),
                "title": str(option.get("label") or payload.get("title") or option.get("id") or "Selected artifact").strip(),
                "description": str(option.get("description") or payload.get("description") or "Selected source-grounded artifact.").strip(),
                "source_columns": [str(value) for value in payload.get("source_columns", []) if str(value).strip()]
                if isinstance(payload.get("source_columns"), list)
                else [],
                "required_source_ids": source_ids[:4],
                "caveats": [],
                "acceptance_criteria": ["Produce exactly this selected artifact with source citations."],
            }
        )
    return {
        "mode": "selected",
        "artifacts": artifacts,
        "rationale": "User selected server-owned artifact options from the prior AI discovery plan.",
        "required_citations": source_ids[:4],
        "caveats": [],
        "acceptance_criteria": ["Produce exactly the selected artifacts and no extras."],
    }


def _chat_to_build_result(chat: ChatResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(chat, ChatResult):
        return {
            "answer": chat.answer,
            "cited_source_ids": chat.cited_source_ids,
            "artifacts": chat.artifacts,
            "unresolved_issues": [],
        }
    artifacts = chat.get("artifacts") if isinstance(chat.get("artifacts"), list) else []
    cited = [int(value) for value in chat.get("cited_source_ids", []) if str(value).isdigit()] if isinstance(chat.get("cited_source_ids"), list) else []
    return {
        "answer": str(chat.get("answer") or ""),
        "cited_source_ids": cited,
        "artifacts": artifacts,
        "unresolved_issues": chat.get("unresolved_issues", []) if isinstance(chat.get("unresolved_issues"), list) else [],
    }


def _provider_failure_diagnostics(
    *,
    stage: str,
    error: Exception,
    source_profile: dict[str, Any],
    artifact_plan: dict[str, Any],
) -> dict[str, Any]:
    error_message = str(error) or error.__class__.__name__
    planner_attempts = getattr(error, "attempts", [])
    if not isinstance(planner_attempts, list):
        planner_attempts = []
    workspace_items = [
        {"path": "/analysis/source-profile.json", "kind": "analysis", "content": source_profile},
        {
            "path": f"/review/artifact-{stage}-failure.json",
            "kind": "review",
            "content": {
                "stage": stage,
                "error": error_message,
                "source_profile_summary": source_profile.get("summary"),
                "artifact_plan": artifact_plan,
            },
        },
    ]
    if artifact_plan:
        workspace_items.insert(1, {"path": "/plan/artifact-plan.json", "kind": "planning", "content": artifact_plan})
    if planner_attempts:
        workspace_items.append({"path": "/review/artifact-planner-attempts.json", "kind": "review", "content": {"attempts": planner_attempts}})
    return {
        "stage": stage,
        "error": error_message,
        "source_profile": {
            "summary": source_profile.get("summary"),
            "diagnostics": source_profile.get("diagnostics", {}),
        },
        "artifact_plan": artifact_plan,
        "workspace_items": workspace_items,
        "repair_attempts": [],
        "planner_attempts": planner_attempts,
        "user_message": (
            "I could not generate artifacts because the model provider did not return a usable artifact "
            f"{'plan' if stage == 'plan' else 'build'} response. I saved the source profile and diagnostics for this run instead of showing an unsafe or fabricated artifact."
        ),
    }


def _contract_warnings(plan: dict[str, Any], build: dict[str, Any], artifacts: list[ValidatedArtifact]) -> list[str]:
    warnings: list[str] = []
    planned = [item for item in plan.get("artifacts", []) if isinstance(item, dict)]
    if not planned:
        return ["Artifact plan did not commit to any artifacts."]
    expected_kinds = [str(item.get("artifact_kind") or "") for item in planned]
    actual_kinds = [artifact.kind for artifact in artifacts]
    if len(actual_kinds) != len(expected_kinds):
        warnings.append(f"Expected exactly {len(expected_kinds)} artifact(s), got {len(actual_kinds)}.")
    for expected, actual in zip(expected_kinds, actual_kinds):
        if expected != actual:
            warnings.append(f"Expected artifact kind {expected}, got {actual}.")
    if not str(build.get("answer") or "").strip():
        warnings.append("Build result did not include an answer.")
    if artifacts and not build.get("cited_source_ids"):
        warnings.append("Build result produced artifacts without citations.")
    return warnings


def _review_failed(report: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict):
        return False
    severity = str(report.get("severity") or "none")
    return severity in {"medium", "high"} or (report.get("passed") is False and severity not in {"none", "low"})


def _plan_citations(plan: dict[str, Any], sources: list[dict[str, Any]]) -> list[int]:
    citations = [int(value) for value in plan.get("required_citations", []) if str(value).isdigit()] if isinstance(plan.get("required_citations"), list) else []
    allowed = {int(source["source_id"]) for source in sources if source.get("source_id") is not None}
    citations = [value for value in citations if value in allowed]
    return citations or ([int(sources[0]["source_id"])] if sources and sources[0].get("source_id") is not None else [])


def _discovery_answer(plan: dict[str, Any]) -> str:
    count = len([item for item in plan.get("artifacts", []) if isinstance(item, dict)])
    return f"I found {count} source-grounded artifact option{'s' if count != 1 else ''}. Select one or more cards to produce the artifacts."


def _public_build(build: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": str(build.get("answer") or ""),
        "cited_source_ids": build.get("cited_source_ids", []),
        "artifact_count": len(build.get("artifacts", [])) if isinstance(build.get("artifacts"), list) else 0,
        "unresolved_issues": build.get("unresolved_issues", []),
    }


def _profile_summary(tables: list[dict[str, Any]], texts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    parts = [f"{len(sources)} source chunk(s)"]
    if tables:
        parts.append(f"{len(tables)} table(s)")
        first = tables[0]
        parts.append(f"{first['row_count']} row(s) and {first['column_count']} column(s) in {first['file_name']}")
    if texts:
        parts.append(f"{len(texts)} text file(s)")
    return "; ".join(parts)


def _sniff_delimiter(text: str, file_name: str) -> str:
    if Path(file_name).suffix.lower() == ".tsv":
        return "\t"
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        return "\t" if "\t" in sample and sample.count("\t") > sample.count(",") else ","


def _number(value: str) -> float | None:
    if re.search(r"[A-Za-z가-힣]", value):
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
    if cleaned in {"", ".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _looks_date(value: str) -> bool:
    stripped = value.strip()
    if re.search(r"\d{4}[-/]\d{1,2}([-/]\d{1,2})?", stripped):
        return True
    try:
        datetime.fromisoformat(stripped)
        return True
    except ValueError:
        return False


def _avg_len(values: list[str]) -> float:
    return sum(len(value) for value in values) / max(1, len(values))


def _top_values(values: list[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return [{"value": label, "count": count} for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]]


def _compact_text(value: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "..."
