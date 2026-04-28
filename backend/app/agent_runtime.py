from __future__ import annotations

from typing import Any

import httpx

from .database import connect
from .meta_issues import capture_internal_issue
from .openrouter import OpenRouterClient, OpenRouterMissingKey, OpenRouterResponseError
from .providers import provider_registry
from .settings_store import current_app_settings, set_provider_verification


MODEL_OUTPUTS = {"answer", "chart", "table", "summary_panel", "file_draft"}
OUTPUT_PRIORITY = ["file_draft", "chart", "table", "summary_panel", "answer"]
SURVEY_PATH_MARKERS = ("csv", "tsv", "plain")


def _ordered_outputs(outputs: list[str]) -> list[str]:
    seen = {output for output in outputs if output in MODEL_OUTPUTS}
    ordered = [output for output in OUTPUT_PRIORITY if output in seen]
    extras = [output for output in outputs if output in MODEL_OUTPUTS and output not in ordered]
    return ordered + extras


def build_capability_snapshot(
    *,
    question: str,
    execution_plan: dict[str, Any],
    planner_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    file_types = [str(item).lower() for item in execution_plan.get("file_types", []) if str(item).strip()]
    requested = [
        str(item)
        for item in ((planner_contract or {}).get("required_outputs") or execution_plan.get("requested_outputs") or [])
        if str(item) in MODEL_OUTPUTS
    ]
    normalized_question = question.lower()
    looks_like_survey_material = (
        execution_plan.get("intent") == "create"
        and any(marker in file_type for marker in SURVEY_PATH_MARKERS for file_type in file_types)
        and any(output in requested for output in ("chart", "file_draft", "summary_panel", "table"))
    ) or "설문" in question or "survey" in normalized_question
    if looks_like_survey_material:
        path = "survey_material"
        guaranteed_outputs = ["file_draft", "chart"]
        optional_outputs = ["table", "summary_panel"]
        repairable_outputs = ["summary_panel"]
    else:
        path = "text_summary"
        guaranteed_outputs = ["answer"]
        optional_outputs = []
        repairable_outputs = []
    return {
        "path": path,
        "question": question,
        "file_types": file_types,
        "requested_outputs": _ordered_outputs(requested),
        "guaranteed_outputs": guaranteed_outputs,
        "optional_outputs": optional_outputs,
        "repairable_outputs": repairable_outputs,
    }


def reconcile_task_contract(
    *,
    question: str,
    planner_contract: dict[str, Any],
    execution_plan: dict[str, Any],
) -> dict[str, Any]:
    capability_snapshot = build_capability_snapshot(
        question=question,
        execution_plan=execution_plan,
        planner_contract=planner_contract,
    )
    desired_outputs = _ordered_outputs(
        [str(item) for item in planner_contract.get("required_outputs", []) if str(item) in MODEL_OUTPUTS]
    )
    guaranteed = set(capability_snapshot["guaranteed_outputs"])
    optional = set(capability_snapshot["optional_outputs"])
    primary_outputs: list[str] = []
    supporting_outputs: list[str] = []
    adjustments: list[str] = []
    normalized_question = question.lower()
    broad_material_request = (
        capability_snapshot["path"] == "survey_material"
        and (
            str(planner_contract.get("deliverable") or "") in {"insight_report", "analysis_material"}
            or any(term in normalized_question for term in ("analysis", "report", "workshop", "deck"))
            or any(term in question for term in ("분석", "자료", "보고서", "워크샵", "설계"))
        )
    )

    if broad_material_request:
        for output in ("file_draft", "chart"):
            if output in guaranteed and output not in primary_outputs:
                primary_outputs.append(output)
                if output not in desired_outputs:
                    adjustments.append(f"Added {output} as a primary output for the survey material bundle.")

    for output in desired_outputs:
        if output == "summary_panel" and capability_snapshot["path"] == "survey_material":
            if output not in supporting_outputs:
                supporting_outputs.append(output)
            adjustments.append(
                "Downgraded summary_panel to a supporting artifact because the survey path guarantees a draft + chart bundle."
            )
            continue
        if output in guaranteed and output not in primary_outputs:
            primary_outputs.append(output)
            continue
        if output in optional and output not in primary_outputs:
            primary_outputs.append(output)
            continue
        if output not in guaranteed and output not in optional:
            adjustments.append(f"Removed unsupported output `{output}` from the executable bundle.")

    if not primary_outputs:
        fallback_primary = [output for output in capability_snapshot["guaranteed_outputs"] if output in MODEL_OUTPUTS]
        primary_outputs = fallback_primary[:1] or ["answer"]
        adjustments.append("Filled the executable bundle with the closest guaranteed output path.")

    executable_contract = {
        **planner_contract,
        "required_outputs": _ordered_outputs(primary_outputs),
        "desired_outputs": desired_outputs,
        "primary_outputs": _ordered_outputs(primary_outputs),
        "supporting_outputs": _ordered_outputs(supporting_outputs),
        "guaranteed_outputs": _ordered_outputs(capability_snapshot["guaranteed_outputs"]),
        "optional_outputs": _ordered_outputs(capability_snapshot["optional_outputs"]),
        "repairable_outputs": _ordered_outputs(capability_snapshot["repairable_outputs"]),
        "capability_snapshot": capability_snapshot,
        "contract_adjustments": adjustments,
    }
    return {
        **executable_contract,
        "planner_contract": planner_contract,
        "executable_contract": dict(executable_contract),
        "contract_adjustments": adjustments,
        "capability_snapshot": capability_snapshot,
    }


def update_contract_user_direction(task_contract: dict[str, Any], user_direction: dict[str, Any]) -> dict[str, Any]:
    updated = dict(task_contract)
    updated["user_direction"] = user_direction
    updated["needs_user_question"] = False
    planner_contract = dict(updated.get("planner_contract") or {})
    if planner_contract:
        planner_contract["user_direction"] = user_direction
        planner_contract["needs_user_question"] = False
        updated["planner_contract"] = planner_contract
    executable_contract = dict(updated.get("executable_contract") or {})
    if executable_contract:
        executable_contract["user_direction"] = user_direction
        executable_contract["needs_user_question"] = False
        updated["executable_contract"] = executable_contract
    return updated


def build_summary_panel_artifact(evidence_packet: dict[str, Any]) -> dict[str, Any] | None:
    dataset = evidence_packet.get("dataset") if isinstance(evidence_packet, dict) else None
    if not isinstance(dataset, dict):
        return None
    subject = str(dataset.get("subject") or dataset.get("file_name") or "자료").strip()
    source_id = dataset.get("source_id")
    source_chunk_id = dataset.get("source_chunk_id")
    theme_counts = evidence_packet.get("theme_counts")
    examples = evidence_packet.get("representative_examples")
    caveats = evidence_packet.get("caveats")
    sections: list[dict[str, str]] = []
    row_count = dataset.get("row_count")
    open_text_count = dataset.get("open_text_question_count")
    if row_count:
        sections.append(
            {
                "heading": "데이터 범위",
                "body": f"{row_count}건의 응답과 주관식 문항 {open_text_count or 0}개를 기준으로 핵심 패턴을 정리했습니다.",
            }
        )
    if isinstance(theme_counts, list) and theme_counts:
        top = [
            f"{item.get('label')}: {item.get('value')}건"
            for item in theme_counts[:3]
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ]
        if top:
            sections.append({"heading": "핵심 주제", "body": ", ".join(top)})
    if isinstance(examples, list) and examples:
        sample = next((item for item in examples if isinstance(item, dict) and item.get("excerpt")), None)
        if sample:
            sections.append(
                {
                    "heading": "대표 응답",
                    "body": f"{sample.get('theme', '기타')} - {str(sample.get('excerpt') or '').strip()}",
                }
            )
    if isinstance(caveats, list) and caveats:
        sections.append({"heading": "해석 주의", "body": str(caveats[0])})
    if not sections:
        return None
    return {
        "kind": "summary_panel",
        "title": f"{subject}: 핵심 요약",
        "caption": "근거 패킷을 바탕으로 정리한 요약 패널입니다.",
        "display_mode": "supporting",
        "source_ids": [source_id] if source_id else [],
        "source_chunk_ids": [source_chunk_id] if source_chunk_id else [],
        "sections": sections,
    }


def file_manifest(session_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.name, f.type, f.status, f.size, f.chunk_count, f.error
            FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE sf.session_id = ?
            ORDER BY sf.attached_at
            """,
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


async def verify_openrouter_provider() -> dict[str, Any]:
    settings = current_app_settings()
    provider = provider_registry().active()
    try:
        result = await provider.verify(
            chat_model=settings["chat_model"],
            embedding_model=settings["embedding_model"],
        )
    except OpenRouterMissingKey as exc:
        set_provider_verification("missing", str(exc))
        capture_internal_issue(
            organization_id="org_single",
            created_by=None,
            source="provider",
            severity="warning",
            title="OpenRouter provider missing key",
            body=str(exc),
        )
        return {"status": "missing", "message": str(exc)}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            message = "OpenRouter rejected the configured API key."
            set_provider_verification("invalid", message)
            capture_internal_issue(
                organization_id="org_single",
                created_by=None,
                source="provider",
                severity="error",
                title=message,
                body=str(exc),
                metadata={"status_code": exc.response.status_code},
            )
            return {"status": "invalid", "message": message, "technical_detail": str(exc)}
        message = f"OpenRouter verification failed with HTTP {exc.response.status_code}."
        set_provider_verification("invalid", message)
        capture_internal_issue(
            organization_id="org_single",
            created_by=None,
            source="provider",
            severity="error",
            title=message,
            body=str(exc),
            metadata={"status_code": exc.response.status_code},
        )
        return {"status": "invalid", "message": message, "technical_detail": str(exc)}
    except OpenRouterResponseError as exc:
        set_provider_verification("invalid", str(exc))
        capture_internal_issue(
            organization_id="org_single",
            created_by=None,
            source="provider",
            severity="error",
            title="OpenRouter response error",
            body=str(exc),
        )
        return {"status": "invalid", "message": str(exc)}
    except Exception as exc:
        message = "OpenRouter verification failed."
        set_provider_verification("invalid", message)
        capture_internal_issue(
            organization_id="org_single",
            created_by=None,
            source="provider",
            severity="error",
            title=message,
            body=str(exc),
        )
        return {"status": "invalid", "message": message, "technical_detail": str(exc)}
    set_provider_verification("verified", str(result.get("message") or "OpenRouter key verified."))
    return result


async def ensure_provider_ready() -> dict[str, Any]:
    settings = current_app_settings()
    status = settings["openrouter_provider_status"]
    if status == "verified":
        return {
            "status": status,
            "message": settings["openrouter_provider_message"],
            "verified_at": settings["openrouter_verified_at"],
        }
    if status in {"missing", "invalid"}:
        return {
            "status": status,
            "message": settings["openrouter_provider_message"] or "OpenRouter provider is not ready.",
        }
    return await verify_openrouter_provider()


def _strings(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    out = [str(item).strip() for item in value if str(item).strip()]
    return out or fallback


def normalize_task_contract(raw: dict[str, Any], *, question: str, fallback_outputs: list[str]) -> dict[str, Any]:
    outputs = [item for item in _strings(raw.get("required_outputs"), fallback_outputs) if item in MODEL_OUTPUTS]
    if not outputs:
        outputs = fallback_outputs if fallback_outputs else ["answer"]
    intent = str(raw.get("intent") or "").strip().lower()
    if intent not in {"ask", "create"}:
        intent = "create" if any(output in outputs for output in ("chart", "table", "summary_panel", "file_draft")) else "ask"
    options = raw.get("question_options")
    if not isinstance(options, list):
        options = []
    normalized_options = []
    for item in options[:4]:
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("id") or item.get("label") or "").strip()
        label = str(item.get("label") or option_id).strip()
        if option_id and label:
            normalized_options.append(
                {
                    "id": option_id,
                    "label": label,
                    "description": str(item.get("description") or "").strip(),
                }
            )
    language = str(raw.get("language") or "").strip() or ("ko" if any("\uac00" <= char <= "\ud7a3" for char in question) else "en")
    return {
        "intent": intent,
        "deliverable": str(raw.get("deliverable") or ("insight_report" if "file_draft" in outputs else outputs[0])).strip(),
        "language": language,
        "required_outputs": outputs,
        "analysis_focus": _strings(raw.get("analysis_focus"), ["evidence"]),
        "success_criteria": _strings(
            raw.get("success_criteria"),
            [
                "final answer directly satisfies the user request",
                "artifacts cite retrieved source chunks",
                "charts use meaningful measures",
            ],
        ),
        "needs_user_question": bool(raw.get("needs_user_question")) and bool(normalized_options),
        "user_question": str(raw.get("user_question") or "").strip(),
        "question_options": normalized_options,
        "default_option": str(raw.get("default_option") or (normalized_options[0]["id"] if normalized_options else "")).strip(),
    }


def chart_uses_suspicious_measure(artifact: Any) -> bool:
    kind = getattr(artifact, "kind", "")
    spec = getattr(artifact, "spec", {})
    if kind != "chart" or not isinstance(spec, dict):
        return False
    y_label = str(spec.get("y_label") or "").lower()
    x_label = str(spec.get("x_label") or "").lower()
    if any(term in y_label for term in ("timestamp", "email", "address", "id", "identifier")):
        return True
    if "timestamp" in x_label and y_label not in {"responses", "count"}:
        return True
    values = spec.get("values")
    if not isinstance(values, list):
        return False
    for item in values:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        value = item.get("value")
        if len(label) > 180:
            return True
        if isinstance(value, (int, float)) and abs(float(value)) > 1_000_000 and "count" not in y_label and "responses" not in y_label:
            return True
    return False


def review_contract_result(
    *,
    task_contract: dict[str, Any],
    answer: str,
    artifacts: list[Any],
    cited_source_ids: list[int],
) -> dict[str, Any]:
    requested_outputs = set(task_contract.get("required_outputs") or [])
    primary_outputs = set(task_contract.get("primary_outputs") or task_contract.get("required_outputs") or [])
    supporting_outputs = set(task_contract.get("supporting_outputs") or [])
    artifact_kinds = {getattr(artifact, "kind", "") for artifact in artifacts}
    failures: list[str] = []
    warnings: list[str] = []
    if not answer.strip():
        failures.append("The final answer is empty.")
    if answer.strip() == "I prepared grounded analysis materials from the attached source data.":
        failures.append("The final answer is a placeholder, not a user-facing result.")
    if primary_outputs & {"chart", "table", "summary_panel", "file_draft"} and not artifacts:
        failures.append("The task requested artifacts, but none passed validation.")
    for expected in primary_outputs & {"chart", "table", "summary_panel", "file_draft"}:
        if expected not in artifact_kinds:
            failures.append(f"Missing required artifact: {expected}.")
    for expected in supporting_outputs & {"chart", "table", "summary_panel", "file_draft"}:
        if expected not in artifact_kinds:
            warnings.append(f"Missing supporting artifact: {expected}.")
    if "file_draft" in requested_outputs:
        draft = next((artifact for artifact in artifacts if getattr(artifact, "kind", "") == "file_draft"), None)
        content = getattr(draft, "spec", {}).get("content") if draft else ""
        title = str(getattr(draft, "title", "") or "").strip()
        filename = str(getattr(draft, "spec", {}).get("filename") if draft else "").strip()
        requires_substantial_draft = str(task_contract.get("deliverable") or "") in {"insight_report", "analysis_material"}
        if not isinstance(content, str) or not content.strip():
            failures.append("The draft is empty.")
        elif requires_substantial_draft and len(content.strip()) < 300:
            failures.append("The draft is too thin to satisfy the requested deliverable.")
        if title in {"분석 자료 초안", "Analysis material", "Analysis draft", "Grounded draft"}:
            failures.append("The draft uses a generic title instead of a subject-specific title.")
        if filename in {"analysis-material.md", "grounded-draft.md", "draft.md"}:
            failures.append("The draft uses a generic filename instead of a subject-specific filename.")
        if isinstance(content, str) and (content.startswith("# 분석 자료") or "open_text, non-empty" in content or "작성 팁:" in content):
            failures.append("The draft repeats raw survey metadata instead of a polished analysis.")
    if "file_draft" in requested_outputs and "chart" in artifact_kinds:
        for artifact in artifacts:
            if getattr(artifact, "kind", "") == "chart" and str(getattr(artifact, "title", "") or "") in {"Survey themes", "Survey chart"}:
                failures.append("A chart uses a generic title instead of a subject-specific title.")
                break
    if "chart" in artifact_kinds:
        for artifact in artifacts:
            if chart_uses_suspicious_measure(artifact):
                failures.append("A chart uses a timestamp, email, id, huge numeric surrogate, or long free-text label as a measure.")
                break
    if artifacts and not cited_source_ids:
        failures.append("Artifacts were produced without source citations.")
    score = max(0.0, 1.0 - (0.25 * len(failures)) - (0.05 * len(warnings)))
    return {
        "passed": not failures,
        "score": round(score, 2),
        "failures": failures,
        "warnings": warnings,
        "artifact_kinds": sorted(artifact_kinds),
        "requested_outputs": sorted(requested_outputs),
        "primary_outputs": sorted(primary_outputs),
        "supporting_outputs": sorted(supporting_outputs),
        "outcome": "needs_revision" if failures else ("completed_with_warning" if warnings else "completed"),
    }
