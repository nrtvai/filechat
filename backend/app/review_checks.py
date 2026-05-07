from __future__ import annotations

from typing import Any

from .models import CheckerReport


def checker_report(
    *,
    phase: str,
    passed: bool = True,
    severity: str = "none",
    findings: list[str] | None = None,
    required_fixes: list[str] | None = None,
    suggested_followups: list[str] | None = None,
    reviewed_output: Any | None = None,
    confidence: str = "medium",
) -> dict[str, Any]:
    return CheckerReport(
        phase=phase,
        passed=passed,
        severity=severity,  # type: ignore[arg-type]
        findings=findings or [],
        required_fixes=required_fixes or [],
        suggested_followups=suggested_followups or [],
        reviewed_output=reviewed_output,
        confidence=confidence,  # type: ignore[arg-type]
    ).model_dump()


def plan_check(task_contract: dict[str, Any]) -> dict[str, Any]:
    findings: list[str] = []
    required_outputs = task_contract.get("required_outputs")
    if not isinstance(required_outputs, list) or not required_outputs:
        findings.append("Task contract has no required outputs.")
    criteria = task_contract.get("success_criteria")
    if not isinstance(criteria, list) or not criteria:
        findings.append("Task contract has no success criteria.")
    return checker_report(
        phase="plan_check",
        passed=not findings,
        severity="medium" if findings else "none",
        findings=findings,
        required_fixes=["Add required outputs and success criteria."] if findings else [],
        confidence="high",
    )


def source_check(sources: list[dict[str, Any]], unavailable: list[str]) -> dict[str, Any]:
    findings: list[str] = []
    if not sources:
        findings.append("No source chunks were available for grounded output.")
    if unavailable:
        findings.append(f"{len(unavailable)} attached file(s) were unavailable.")
    return checker_report(
        phase="source_check",
        passed=bool(sources),
        severity="high" if not sources else "low" if unavailable else "none",
        findings=findings,
        required_fixes=["Load at least one ready source before writing."] if not sources else [],
        confidence="high",
    )


def analysis_check(insight_brief: dict[str, Any], evidence_packet: dict[str, Any]) -> dict[str, Any]:
    findings: list[str] = []
    quality = insight_brief.get("quality_review") if isinstance(insight_brief, dict) else {}
    if isinstance(quality, dict) and quality.get("passed") is False:
        findings.extend(str(item) for item in quality.get("warnings", []) if str(item).strip())
    if insight_brief and not insight_brief.get("insights") and not evidence_packet:
        findings.append("Analysis produced no insight brief insights or evidence packet.")
    return checker_report(
        phase="analysis_check",
        passed=not findings,
        severity="medium" if findings else "none",
        findings=findings,
        suggested_followups=["Use source-level summary only if no analytical pattern is detected."] if findings else [],
        confidence="medium",
    )


def artifact_check(artifacts: list[Any], warnings: list[str], requested_outputs: list[str]) -> dict[str, Any]:
    findings = list(warnings)
    artifact_kinds = {str(getattr(artifact, "kind", "")) for artifact in artifacts}
    requested_artifacts = {item for item in requested_outputs if item in {"chart", "table", "summary_panel", "file_draft", "decision_cards"}}
    missing = sorted(requested_artifacts - artifact_kinds)
    for kind in missing:
        findings.append(f"Missing requested artifact: {kind}.")
    high = bool(missing)
    return checker_report(
        phase="artifact_check",
        passed=not findings,
        severity="high" if high else "low" if findings else "none",
        findings=findings,
        required_fixes=[f"Create missing artifacts: {', '.join(missing)}."] if missing else [],
        confidence="high",
    )


def writing_check(answer: str, artifacts: list[Any]) -> dict[str, Any]:
    findings: list[str] = []
    stripped = answer.strip()
    if not stripped:
        findings.append("Answer draft is empty.")
    if stripped.lower() in {"analysis draft", "data summary", "summary"}:
        findings.append("Answer draft is generic.")
    return checker_report(
        phase="writing_check",
        passed=not findings,
        severity="high" if not stripped else "medium" if findings else "none",
        findings=findings,
        required_fixes=["Write a grounded answer before persistence."] if not stripped else [],
        reviewed_output={"answer": answer, "artifact_count": len(artifacts)},
        confidence="high",
    )
