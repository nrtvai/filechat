from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import get_settings
from .prompt_context import prompt_pack
from .settings_store import get_openrouter_key
from .usage import UsageInfo, usage_from_response

OPENROUTER_URL = "https://openrouter.ai/api/v1"
ARTIFACT_PLANNER_FALLBACK_MODELS = [
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash",
    "anthropic/claude-sonnet-4.5",
]
ARTIFACT_PLANNER_HEALTH_TTL_SECONDS = 30 * 60
_ARTIFACT_PLANNER_UNHEALTHY_UNTIL: dict[str, float] = {}
_ARTIFACT_PLANNER_LAST_SUCCESS: tuple[str, float] | None = None


class OpenRouterMissingKey(RuntimeError):
    pass


class OpenRouterResponseError(RuntimeError):
    pass


class OpenRouterArtifactPlanError(OpenRouterResponseError):
    def __init__(self, message: str, *, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass
class ChatResult:
    answer: str
    cited_source_ids: list[int]
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    usage: UsageInfo = field(default_factory=UsageInfo)


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    usage: UsageInfo


class OpenRouterClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self) -> dict[str, str]:
        key, _ = get_openrouter_key()
        if not key and not self.settings.filechat_allow_fake_openrouter:
            raise OpenRouterMissingKey("OpenRouter API key is not configured.")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:5173",
            "X-Title": "FileChat",
        }

    def _fake_embedding(self, text: str, dimensions: int = 128) -> list[float]:
        values = [0.0 for _ in range(dimensions)]
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            idx = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16) % dimensions
            values[idx] += 1.0
        norm = sum(v * v for v in values) ** 0.5 or 1
        return [v / norm for v in values]

    async def embeddings(self, inputs: list[str], model: str) -> list[list[float]]:
        result = await self.embedding_result(inputs, model)
        return result.vectors

    async def embedding_result(self, inputs: list[str], model: str) -> EmbeddingResult:
        if self.settings.filechat_allow_fake_openrouter:
            return EmbeddingResult(
                vectors=[self._fake_embedding(text) for text in inputs],
                model=model,
                usage=UsageInfo(),
            )
        payload: dict[str, Any] = {
            "model": model,
            "input": inputs,
            "provider": {
                "allow_fallbacks": True
            },
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{OPENROUTER_URL}/embeddings", headers=self._headers(), json=payload)
            response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            detail = self._error_detail(payload)
            raise OpenRouterResponseError(
                f"Embedding model returned no vectors for `{model}`." + (f" {detail}" if detail else "")
            )
        vectors: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise OpenRouterResponseError(f"Embedding model returned an invalid vector payload for `{model}`.")
            vectors.append(item["embedding"])
        if len(vectors) != len(inputs):
            raise OpenRouterResponseError(f"Embedding model returned {len(vectors)} vector(s) for {len(inputs)} input(s).")
        return EmbeddingResult(
            vectors=vectors,
            model=str(payload.get("model") or model),
            usage=usage_from_response(payload, pricing=await self.model_pricing(model)),
        )

    async def models(self, kind: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{OPENROUTER_URL}/models", headers=self._headers())
            response.raise_for_status()
        models = response.json().get("data", [])
        return [self._normalize_model(item) for item in models if self._model_matches_kind(item, kind)]

    async def verify_provider(self, *, chat_model: str, embedding_model: str) -> dict[str, Any]:
        if self.settings.filechat_allow_fake_openrouter:
            return {
                "status": "verified",
                "message": "Fake OpenRouter mode is enabled for local development.",
                "models_checked": [],
            }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{OPENROUTER_URL}/models", headers=self._headers())
            response.raise_for_status()
        payload = response.json()
        models = payload.get("data")
        if not isinstance(models, list) or not models:
            raise OpenRouterResponseError("OpenRouter model metadata response did not include models.")
        model_ids = {str(item.get("id") or "") for item in models if isinstance(item, dict)}
        missing = [model for model in (chat_model,) if model and model not in model_ids]
        embedding_probe = await self.embedding_result(["OpenRouter verification"], embedding_model)
        message = "OpenRouter key verified."
        if missing:
            message = f"OpenRouter key verified, but selected model metadata was not found for: {', '.join(missing)}."
        return {
            "status": "verified",
            "message": message,
            "models_checked": [chat_model, embedding_model],
            "embedding_dimensions": len(embedding_probe.vectors[0]) if embedding_probe.vectors else 0,
            "missing_models": missing,
        }

    async def plan_task(
        self,
        *,
        model: str,
        question: str,
        file_manifest: list[dict[str, Any]],
        prior_answers: list[dict[str, Any]] | None = None,
        prompt_context: dict[str, Any] | None = None,
        reasoning_effort: str = "none",
    ) -> dict[str, Any]:
        if self.settings.filechat_allow_fake_openrouter:
            broad = any(term in question for term in ("분석 자료", "자료 제작", "보고서")) or any(
                term in question.lower() for term in ("analysis material", "make analysis", "insight report")
            )
            normalized = question.lower()
            requested_outputs: list[str] = []
            if broad:
                requested_outputs = ["file_draft", "chart", "table"]
            else:
                if any(term in normalized for term in ("chart", "graph", "plot")) or any(term in question for term in ("차트", "그래프")):
                    requested_outputs.append("chart")
                if any(term in normalized for term in ("table", "comparison")) or "표" in question:
                    requested_outputs.append("table")
                if any(term in normalized for term in ("draft", "report", "document", "new file")) or any(term in question for term in ("초안", "보고서", "문서", "자료")):
                    requested_outputs.append("file_draft")
                if not requested_outputs:
                    requested_outputs = ["answer"]
            has_answer = bool(prior_answers)
            return {
                "intent": "create" if broad or any(term in question.lower() for term in ("make", "create", "chart", "report")) else "ask",
                "deliverable": "insight_report" if broad else "answer",
                "language": "ko" if any("\uac00" <= char <= "\ud7a3" for char in question) else "en",
                "required_outputs": requested_outputs,
                "analysis_focus": ["themes", "evidence", "recommendations"] if broad else ["answer"],
                "success_criteria": [
                    "final answer directly satisfies the user request",
                    "charts use meaningful measures rather than timestamps, emails, or identifiers",
                    "artifacts cite retrieved source chunks",
                ],
                "needs_user_question": broad and not has_answer,
                "user_question": "어떤 의사결정에 바로 쓸 수 있는 분석 자료가 필요하신가요?",
                "question_options": [
                    {"id": "leadership_report", "label": "리더 공유용", "description": "핵심 인사이트와 실행 제안을 우선합니다."},
                    {"id": "team_workshop", "label": "팀 워크숍용", "description": "토론 질문과 병목 유형을 우선합니다."},
                    {"id": "data_review", "label": "데이터 검토용", "description": "근거 표와 분포를 우선합니다."},
                ],
                "default_option": "leadership_report",
            }

        system, user = prompt_pack(
            "planner",
            prompt_context
            or {
                "current_request": question,
                "file_intelligence": {"files": file_manifest},
                "conversation_tail": [],
            },
            inputs={
                "user_request": question,
                "file_manifest": file_manifest,
                "prior_planning_answers": prior_answers or [],
            },
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        if reasoning_effort and reasoning_effort != "none":
            payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{OPENROUTER_URL}/chat/completions", headers=self._headers(), json=payload)
            response.raise_for_status()
        response_payload = response.json()
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterResponseError(f"Selected planner model `{model}` did not return a completion choice.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterResponseError(f"Selected planner model `{model}` returned an empty task contract.")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenRouterResponseError(f"Selected planner model `{model}` did not return valid task-contract JSON.") from exc
        if not isinstance(parsed, dict):
            raise OpenRouterResponseError(f"Selected planner model `{model}` returned an invalid task contract.")
        return parsed

    async def model_pricing(self, model: str) -> dict[str, Any]:
        try:
            models = await self.models("all")
        except Exception:
            return {}
        for item in models:
            if item["id"] == model:
                return item.get("pricing", {})
        return {}

    def _normalize_model(self, item: dict[str, Any]) -> dict[str, Any]:
        pricing = item.get("pricing") or {}
        return {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or item.get("id") or ""),
            "context_length": item.get("context_length"),
            "pricing": {
                "prompt": self._float(pricing.get("prompt")),
                "completion": self._float(pricing.get("completion")),
                "request": self._float(pricing.get("request")),
                "image": self._float(pricing.get("image")),
            },
            "created": item.get("created"),
            "architecture": item.get("architecture") or {},
            "supported_parameters": item.get("supported_parameters") or [],
        }

    def _model_matches_kind(self, item: dict[str, Any], kind: str) -> bool:
        if kind == "all":
            return True
        architecture = item.get("architecture") or {}
        output_modalities = architecture.get("output_modalities") or []
        input_modalities = architecture.get("input_modalities") or []
        if kind == "embedding":
            return "embeddings" in output_modalities
        if kind == "chat":
            return "text" in output_modalities and "text" in input_modalities
        return False

    def _float(self, value: Any) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _error_detail(self, payload: dict[str, Any]) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("code")
            return str(detail) if detail else ""
        if isinstance(error, str):
            return error
        return ""

    async def chat(
        self,
        *,
        model: str,
        question: str,
        sources: list[dict[str, Any]],
        unavailable: list[str],
        history: list[dict[str, str]] | None = None,
        prompt_context: dict[str, Any] | None = None,
        use_web_search: bool = False,
        web_search_engine: str = "auto",
        reasoning_effort: str = "none",
    ) -> ChatResult:
        if self.settings.filechat_allow_fake_openrouter:
            artifacts = self._fake_artifacts(question, sources)
            answer = "I found support in the attached sources. " + sources[0]["excerpt"]
            if artifacts:
                answer = "I created a grounded artifact from the attached sources."
            if unavailable:
                answer += f"\n\nNote: {len(unavailable)} attached file(s) were still unavailable for this answer."
            return ChatResult(answer=answer, cited_source_ids=[sources[0]["source_id"]], artifacts=artifacts, model=model, usage=UsageInfo())

        source_block = "\n\n".join(f"[source {s['source_id']}] {s['file_name']} · {s['location']}\n{s['content']}" for s in sources)
        unavailable_note = ""
        if unavailable:
            unavailable_note = f"\nUnavailable attached file ids: {', '.join(unavailable)}"
        history_block = ""
        if history:
            history_block = "\n\nRecent conversation:\n" + "\n".join(
                f"{item['role']}: {item['content']}" for item in history[-8:]
            )
        system, base_user = prompt_pack(
            "grounded_answer",
            prompt_context or {"current_request": question, "conversation_tail": history or []},
            inputs={
                "question": question,
                "unavailable_file_ids": unavailable,
                "source_contract": (
                    "Return JSON with keys answer, cited_source_ids, and optional artifacts. "
                    "cited_source_ids must be source numbers used. Artifact kinds: mermaid, chart, table, decision_cards, comparison, summary_panel, file_draft. "
                    "Charts require chart_type bar|line|pie, x_label, y_label, values [{label,value,source_id}]. "
                    "For roadmap, gantt, or timeline outputs, do not use chart_type timeline; use summary_panel or decision_cards with a jsonRenderSpec Timeline component. "
                    "File drafts require filename, format, content. Requests for Notion documents must use file_draft plus Notion export or publish targets, not a new artifact kind."
                ),
            },
        )
        user = f"{base_user}\n\nQuestion: {question}{history_block}\n{unavailable_note}\n\nSources:\n{source_block}"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        if reasoning_effort and reasoning_effort != "none":
            payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}
        if use_web_search:
            payload["tools"] = [
                {
                    "type": "openrouter:web_search",
                    "parameters": {
                        "engine": web_search_engine,
                        "max_results": 5,
                        "max_total_results": 10,
                    },
                }
            ]
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{OPENROUTER_URL}/chat/completions", headers=self._headers(), json=payload)
            response.raise_for_status()
        response_payload = response.json()
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            detail = self._error_detail(response_payload)
            raise OpenRouterResponseError(
                f"Selected chat model `{model}` did not return a completion choice." + (f" {detail}" if detail else "")
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterResponseError(f"Selected chat model `{model}` returned an empty response.")
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("top-level response was not an object")
            answer = str(parsed.get("answer", "")).strip()
            cited = [int(v) for v in parsed.get("cited_source_ids", []) if str(v).isdigit()]
            artifacts = parsed.get("artifacts", [])
            if not isinstance(artifacts, list):
                artifacts = []
        except Exception as exc:
            raise OpenRouterResponseError(
                f"Selected chat model `{model}` did not return structured output that FileChat can use."
            ) from exc
        if not answer:
            answer = "I could not find that answer in the attached sources."
        if not cited and "not found in the attached sources" not in answer.lower() and "could not find" not in answer.lower():
            cited = [sources[0]["source_id"]]
        return ChatResult(
            answer=answer,
            cited_source_ids=cited,
            artifacts=artifacts,
            model=str(response_payload.get("model") or model),
            usage=usage_from_response(response_payload, pricing=await self.model_pricing(model)),
        )

    async def write_draft_from_evidence(
        self,
        *,
        model: str,
        question: str,
        prompt_context: dict[str, Any],
        evidence_packet: dict[str, Any],
        sources: list[dict[str, Any]],
        reasoning_effort: str = "none",
    ) -> ChatResult:
        if self.settings.filechat_allow_fake_openrouter:
            title = str(evidence_packet.get("recommended_title") or "Survey analysis draft")
            filename = str(evidence_packet.get("recommended_filename") or "survey-analysis-draft.md")
            content = _fallback_draft_content(evidence_packet)
            return ChatResult(
                answer="분석 초안을 근거 패킷에 맞춰 정리했습니다.",
                cited_source_ids=[sources[0]["source_id"]] if sources else [1],
                artifacts=[
                    {
                        "kind": "file_draft",
                        "title": title,
                        "caption": str(evidence_packet.get("draft_caption") or "Evidence-grounded Markdown draft."),
                        "display_mode": "primary",
                        "source_ids": [sources[0]["source_id"]] if sources else [1],
                        "source_chunk_ids": [sources[0]["chunk_id"]] if sources else [],
                        "filename": filename,
                        "format": "markdown",
                        "content": content,
                    }
                ],
                model=model,
                usage=UsageInfo(),
            )

        source_refs = [{"source_id": source["source_id"], "file_name": source["file_name"], "location": source["location"]} for source in sources]
        system, user = prompt_pack(
            "draft_writer",
            prompt_context,
            inputs={"question": question, "evidence_packet": evidence_packet, "source_refs": source_refs},
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        if reasoning_effort and reasoning_effort != "none":
            payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{OPENROUTER_URL}/chat/completions", headers=self._headers(), json=payload)
            response.raise_for_status()
        response_payload = response.json()
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterResponseError(f"Selected draft model `{model}` did not return a completion choice.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterResponseError(f"Selected draft model `{model}` returned an empty draft.")
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("top-level response was not an object")
            draft = parsed.get("draft")
            if not isinstance(draft, dict):
                raise ValueError("draft was not an object")
        except Exception as exc:
            raise OpenRouterResponseError(f"Selected draft model `{model}` did not return structured draft JSON.") from exc

        title = str(draft.get("title") or evidence_packet.get("recommended_title") or "Analysis draft").strip()
        filename = str(draft.get("filename") or evidence_packet.get("recommended_filename") or "analysis-draft.md").strip()
        draft_content = str(draft.get("content") or "").strip()
        if not draft_content:
            draft_content = _fallback_draft_content(evidence_packet)
        recommended_title = str(evidence_packet.get("recommended_title") or "").strip()
        if recommended_title and (title.lower() in {"analysis draft", "survey analysis", "분석 자료", "분석 자료 초안"}):
            title = recommended_title
        if recommended_title and draft_content.startswith("# 분석 자료"):
            draft_content = re.sub(r"^# .+", f"# {recommended_title}", draft_content, count=1)
        cited = [int(v) for v in parsed.get("cited_source_ids", []) if str(v).isdigit()]
        if not cited and sources:
            cited = [sources[0]["source_id"]]
        return ChatResult(
            answer=str(parsed.get("answer") or "분석 초안을 근거 패킷에 맞춰 정리했습니다.").strip(),
            cited_source_ids=cited,
            artifacts=[
                {
                    "kind": "file_draft",
                    "title": title,
                    "caption": str(draft.get("caption") or evidence_packet.get("draft_caption") or ""),
                    "display_mode": "primary",
                    "source_ids": cited,
                    "filename": filename,
                    "format": "markdown",
                    "content": draft_content,
                }
            ],
            model=str(response_payload.get("model") or model),
            usage=usage_from_response(response_payload, pricing=await self.model_pricing(model)),
        )

    async def plan_artifacts(
        self,
        *,
        model: str,
        question: str,
        task_contract: dict[str, Any],
        source_profile: dict[str, Any],
        prompt_context: dict[str, Any],
        selected_options: list[dict[str, Any]] | None = None,
        discovery_only: bool = False,
        reasoning_effort: str = "none",
    ) -> dict[str, Any]:
        if self.settings.filechat_allow_fake_openrouter:
            return _fake_artifact_plan(
                question=question,
                task_contract=task_contract,
                source_profile=source_profile,
                selected_options=selected_options or [],
                discovery_only=discovery_only,
            )
        response_format = _artifact_plan_response_format()
        candidates = await self._artifact_planner_candidates(model)
        attempts: list[dict[str, Any]] = []
        failure_context: dict[str, Any] | None = None
        attempt_number = 0
        for candidate in candidates:
            tactics = ["selected_strict"] if candidate == model else ["fallback_model"]
            if candidate == model:
                tactics.append("recovery_prompt")
            for tactic in tactics:
                attempt_number += 1
                parsed: dict[str, Any] | None = None
                try:
                    parsed = await self._json_completion(
                        model=candidate,
                        system=_artifact_planner_system(tactic=tactic),
                        user=_artifact_planner_user(
                            question=question,
                            task_contract=task_contract,
                            source_profile=source_profile,
                            prompt_context=prompt_context,
                            selected_options=selected_options or [],
                            discovery_only=discovery_only,
                            recovery_context=failure_context,
                        ),
                        temperature=0.0 if tactic == "recovery_prompt" else 0.1,
                        response_format=response_format,
                        require_parameters=True,
                        reasoning_effort="none",
                    )
                    plan = _extract_artifact_plan(parsed, model=candidate)
                    _mark_artifact_planner_success(candidate)
                    attempts.append(
                        _artifact_planner_attempt(
                            attempt_number=attempt_number,
                            model=candidate,
                            tactic=tactic,
                            status="passed",
                            parsed=parsed,
                            error="",
                        )
                    )
                    return {
                        **plan,
                        "_planner_attempts": attempts,
                        "_effective_planner_model": candidate,
                    }
                except Exception as exc:
                    response_class = _artifact_plan_response_class(parsed)
                    attempt = _artifact_planner_attempt(
                        attempt_number=attempt_number,
                        model=candidate,
                        tactic=tactic,
                        status="failed",
                        parsed=parsed,
                        error=str(exc) or exc.__class__.__name__,
                        response_class=response_class,
                    )
                    attempts.append(attempt)
                    _mark_artifact_planner_unhealthy(candidate, response_class)
                    failure_context = {
                        "failed_model": candidate,
                        "failed_tactic": tactic,
                        "failure_class": response_class,
                        "error": attempt["error"],
                        "response_keys": attempt.get("response_keys", []),
                        "instruction": "Produce a filled artifact plan. Do not echo or describe the schema.",
                    }
                    if tactic == "selected_strict" and candidate == model:
                        continue
                    break
        tried = ", ".join(dict.fromkeys(str(attempt.get("model") or "") for attempt in attempts if attempt.get("model")))
        raise OpenRouterArtifactPlanError(
            f"Artifact planner could not produce a valid plan after live model recovery attempts. Tried: {tried}.",
            attempts=attempts,
        )

    async def build_artifacts(
        self,
        *,
        model: str,
        question: str,
        artifact_plan: dict[str, Any],
        source_profile: dict[str, Any],
        sources: list[dict[str, Any]],
        prompt_context: dict[str, Any],
        reasoning_effort: str = "none",
    ) -> ChatResult:
        if self.settings.filechat_allow_fake_openrouter:
            return _fake_build_artifacts(
                question=question,
                artifact_plan=artifact_plan,
                source_profile=source_profile,
                sources=sources,
                model=model,
            )
        system = (
            "You are FileChat's artifact builder. Build exactly the planned artifacts using cited retrieved sources. "
            "Use generic tools only: grouping, sums, counts, averages, min/max, top categories, and time bucketing. "
            "Do not dump raw files. Return JSON only."
        )
        user = json.dumps(
            {
                "request": question,
                "artifact_plan": artifact_plan,
                "source_profile": source_profile,
                "source_contract": (
                    "Return keys answer, cited_source_ids, artifacts, unresolved_issues. "
                    "Allowed artifact kinds: mermaid, chart, table, decision_cards, comparison, summary_panel, file_draft. "
                    "Charts require chart_type bar|line|pie and values. Tables require columns and rows. "
                    "Summary panels require sections or jsonRenderSpec. File drafts require filename, format, content."
                ),
                "sources": [
                    {
                        "source_id": source.get("source_id"),
                        "file_name": source.get("file_name"),
                        "location": source.get("location"),
                        "content": source.get("content"),
                    }
                    for source in sources[:12]
                ],
                "prompt_context": prompt_context,
            },
            ensure_ascii=False,
        )
        parsed = await self._json_completion(model=model, system=system, user=user, temperature=0.1)
        return _chat_result_from_artifact_payload(parsed, model=model)

    async def repair_artifacts(
        self,
        *,
        model: str,
        question: str,
        artifact_plan: dict[str, Any],
        source_profile: dict[str, Any],
        sources: list[dict[str, Any]],
        current_build: dict[str, Any],
        validation_warnings: list[str],
        red_team_report: dict[str, Any],
        prompt_context: dict[str, Any],
        repair_attempt: int,
        reasoning_effort: str = "none",
    ) -> ChatResult:
        if self.settings.filechat_allow_fake_openrouter:
            return _fake_build_artifacts(
                question=question,
                artifact_plan=artifact_plan,
                source_profile=source_profile,
                sources=sources,
                model=model,
            )
        system = (
            "You repair FileChat artifact builds. Use the artifact plan, source profile, validator warnings, and red-team findings. "
            "Return a corrected complete build result. Do not add artifacts beyond the plan. Return JSON only."
        )
        user = json.dumps(
            {
                "request": question,
                "repair_attempt": repair_attempt,
                "artifact_plan": artifact_plan,
                "source_profile": source_profile,
                "current_build": current_build,
                "validation_warnings": validation_warnings,
                "red_team_report": red_team_report,
                "sources": [
                    {
                        "source_id": source.get("source_id"),
                        "file_name": source.get("file_name"),
                        "location": source.get("location"),
                        "content": source.get("content"),
                    }
                    for source in sources[:12]
                ],
                "prompt_context": prompt_context,
            },
            ensure_ascii=False,
        )
        parsed = await self._json_completion(model=model, system=system, user=user, temperature=0.0)
        return _chat_result_from_artifact_payload(parsed, model=model)

    async def review_phase(
        self,
        *,
        model: str,
        phase: str,
        phase_goal: str,
        task_contract: dict[str, Any],
        evidence_packet: dict[str, Any],
        source_refs: list[dict[str, Any]],
        artifact_specs: list[dict[str, Any]],
        answer_draft: str,
        prior_checker_reports: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self.settings.filechat_allow_fake_openrouter:
            return {
                "phase": phase,
                "passed": True,
                "severity": "none",
                "findings": [],
                "required_fixes": [],
                "suggested_followups": [],
                "confidence": "high",
            }
        context = {
            "phase_goal": phase_goal,
            "task_contract": task_contract,
            "source_refs": source_refs,
            "prior_checker_reports": prior_checker_reports or [],
        }
        system, user = prompt_pack(
            "red_team_review",
            context,
            inputs={
                "phase": phase,
                "evidence_packet": evidence_packet,
                "artifact_specs": artifact_specs,
                "answer_draft": answer_draft,
            },
        )
        return await self._json_completion(model=model, system=system, user=user, temperature=0.0)

    async def proofread_output(
        self,
        *,
        model: str,
        answer_draft: str,
        insight_narrative: dict[str, Any] | None,
        red_team_findings: list[str],
        evidence_packet: dict[str, Any],
    ) -> dict[str, Any]:
        if self.settings.filechat_allow_fake_openrouter:
            return {"answer": answer_draft, "insight_narrative": insight_narrative or {}}
        system, user = prompt_pack(
            "proofread_editor",
            {
                "strict_instruction": "Do not add new claims.",
                "evidence_packet": evidence_packet,
            },
            inputs={
                "answer_draft": answer_draft,
                "insight_narrative": insight_narrative or {},
                "red_team_findings": red_team_findings,
            },
        )
        return await self._json_completion(model=model, system=system, user=user, temperature=0.1)

    async def _json_completion(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        response_format: dict[str, Any] | None = None,
        require_parameters: bool = False,
        reasoning_effort: str = "none",
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": response_format or {"type": "json_object"},
            "temperature": temperature,
        }
        if require_parameters:
            payload["provider"] = {"require_parameters": True}
        if reasoning_effort and reasoning_effort != "none":
            payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{OPENROUTER_URL}/chat/completions", headers=self._headers(), json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = ""
                try:
                    detail = self._error_detail(response.json())
                except Exception:
                    detail = response.text[:500]
                raise OpenRouterResponseError(
                    f"Selected model `{model}` request failed with HTTP {response.status_code}."
                    + (f" {detail}" if detail else "")
                ) from exc
        response_payload = response.json()
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterResponseError(f"Selected model `{model}` did not return a completion choice.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterResponseError(f"Selected model `{model}` returned an empty JSON response.")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenRouterResponseError(f"Selected model `{model}` did not return valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise OpenRouterResponseError(f"Selected model `{model}` returned a non-object JSON payload.")
        return parsed

    async def _artifact_planner_candidates(self, selected_model: str) -> list[str]:
        selected = str(selected_model or "").strip()
        supported = await self._structured_output_model_ids()
        ordered: list[str] = []
        if selected and _artifact_planner_model_healthy(selected):
            ordered.append(selected)
        last_success = _artifact_planner_last_success_model()
        if last_success and last_success != selected and _artifact_planner_model_healthy(last_success):
            ordered.append(last_success)
        for fallback in ARTIFACT_PLANNER_FALLBACK_MODELS:
            if fallback not in ordered:
                ordered.append(fallback)
        if selected and selected not in ordered:
            ordered.insert(0, selected)
        if supported is not None:
            ordered = [candidate for candidate in ordered if candidate in supported or candidate == selected]
        return list(dict.fromkeys(candidate for candidate in ordered if candidate))

    async def _structured_output_model_ids(self) -> set[str] | None:
        try:
            models = await self.models("chat")
        except Exception:
            return None
        supported: set[str] = set()
        for item in models:
            params = set(item.get("supported_parameters") or [])
            if {"response_format", "structured_outputs"} <= params:
                supported.add(str(item.get("id") or ""))
        return supported

    def _fake_artifacts(self, question: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = question.lower()
        source = sources[0]
        source_id = source["source_id"]
        chunk_id = str(source.get("chunk_id") or "")
        values = _fake_chart_values(source)
        if "mermaid" in normalized or "flowchart" in normalized:
            return [
                {
                    "kind": "mermaid",
                    "title": "Source process flow",
                    "caption": "Generated from retrieved source rows.",
                    "source_ids": [source_id],
                    "diagram": "flowchart TD\n  Source[Source records] --> Review[Review values]\n  Review --> Action[Create follow-up]\n  Action --> Confirm[Confirm status]",
                }
            ]
        if "timeline" in normalized or "roadmap" in normalized:
            return [
                {
                    "kind": "summary_panel",
                    "title": "Source timeline",
                    "caption": "Roadmap rendered as a JSON artifact.",
                    "source_ids": [source_id],
                    "jsonRenderSpec": {
                        "root": "card",
                        "elements": {
                            "card": {"type": "ArtifactCard", "props": {"title": "Source timeline"}, "children": ["timeline"]},
                            "timeline": {
                                "type": "Timeline",
                                "props": {
                                    "items": [
                                        {
                                            "label": "Initial review",
                                            "date": "2026-05-06",
                                            "description": "Review source-grounded priorities.",
                                            "sourceChunkId": chunk_id,
                                        },
                                        {
                                            "label": "Follow-up review",
                                            "date": "2026-06-03",
                                            "description": "Review unresolved questions and next actions.",
                                            "sourceChunkId": chunk_id,
                                        },
                                    ]
                                },
                                "children": [],
                            },
                        },
                    },
                }
            ]
        if "decision" in normalized or "option" in normalized:
            return [
                {
                    "kind": "decision_cards",
                    "title": "Decision options",
                    "caption": "Choices grounded in retrieved rows.",
                    "source_ids": [source_id],
                    "jsonRenderSpec": {
                        "root": "card",
                        "elements": {
                            "card": {"type": "ArtifactCard", "props": {"title": "Decision options"}, "children": ["stack"]},
                            "stack": {"type": "Stack", "props": {"gap": "sm"}, "children": ["one", "two"]},
                            "one": {"type": "TextBlock", "props": {"text": "Review records with the strongest signal."}, "children": []},
                            "two": {"type": "TextBlock", "props": {"text": "Prioritize rows with missing or urgent follow-up."}, "children": []},
                        },
                    },
                }
            ]
        if "summary panel" in normalized or "summary" in normalized:
            return [
                {
                    "kind": "summary_panel",
                    "title": "Source summary",
                    "caption": "Grounded summary panel.",
                    "source_ids": [source_id],
                    "sections": [
                        {"heading": "Scope", "body": "The panel summarizes retrieved source rows."},
                        {"heading": "Pattern", "body": "Review the largest or most frequent values first."},
                    ],
                }
            ]
        if "table" in normalized or "comparison" in normalized:
            return [
                {
                    "kind": "table",
                    "title": "Record comparison",
                    "caption": "A compact table from retrieved source rows.",
                    "source_ids": [source_id],
                    "columns": ["Record", "Signal", "Action"],
                    "rows": [["Record A", "Largest value", "Review"], ["Record B", "Missing context", "Follow up"]],
                }
            ]
        if any(word in normalized for word in ("chart", "graph", "plot")):
            if values:
                chart_type = "line" if "line" in normalized else "pie" if "pie" in normalized else "bar"
                return [
                    {
                        "kind": "chart",
                        "title": "Survey chart",
                        "caption": "Generated from retrieved source rows.",
                        "source_ids": [source_id],
                        "chart_type": chart_type,
                        "x_label": "Response",
                        "y_label": "Count",
                        "values": values,
                    }
                ]
        if any(phrase in normalized for phrase in ("new file", "draft", "write a file", "create a file", "report", "notion")):
            return [
                {
                    "kind": "file_draft",
                    "title": "Grounded draft",
                    "caption": "Drafted from the attached source.",
                    "source_ids": [source_id],
                    "filename": "grounded-draft.md",
                    "format": "markdown",
                    "content": f"# Grounded Draft\n\n{source['excerpt']}\n\n| Record | Action |\n| --- | --- |\n| Record A | Review |\n| Record B | Follow up |",
                }
            ]
        return []


def _chat_result_from_artifact_payload(parsed: dict[str, Any], *, model: str) -> ChatResult:
    artifacts = _normalize_output_artifacts(parsed.get("artifacts"))
    cited = [int(value) for value in parsed.get("cited_source_ids", []) if str(value).isdigit()] if isinstance(parsed.get("cited_source_ids"), list) else []
    return ChatResult(
        answer=str(parsed.get("answer") or "").strip() or "I created the requested source-grounded artifacts.",
        cited_source_ids=cited,
        artifacts=artifacts,
        model=model,
        usage=UsageInfo(),
    )


def _normalize_output_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    artifacts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        artifact = dict(item)
        if not str(artifact.get("kind") or "").strip() and str(artifact.get("artifact_kind") or "").strip():
            artifact["kind"] = str(artifact["artifact_kind"]).strip()
        if not str(artifact.get("caption") or "").strip() and str(artifact.get("description") or "").strip():
            artifact["caption"] = str(artifact["description"]).strip()
        if not isinstance(artifact.get("source_ids"), list) and isinstance(artifact.get("required_source_ids"), list):
            artifact["source_ids"] = artifact["required_source_ids"]
        if artifact.get("kind") == "chart" and not isinstance(artifact.get("values"), list):
            data = artifact.get("data")
            if isinstance(data, list):
                artifact["values"] = data
        artifacts.append(artifact)
    return artifacts


def _artifact_planner_system(*, tactic: str) -> str:
    recovery_note = ""
    if tactic == "recovery_prompt":
        recovery_note = "You are recovering from a failed artifact planning attempt. Correct the failure and produce the populated plan only. "
    elif tactic == "fallback_model":
        recovery_note = "A prior planner model failed. Produce the populated artifact plan using the source profile and recovery context. "
    return (
        recovery_note
        + "You are FileChat's artifact planner. Decide which source-grounded artifacts are possible from the provided generic source profile. "
        "Do not use file-name templates or hard-coded business domains. Do not echo, describe, or wrap the JSON schema. "
        "Return a filled artifact plan that satisfies the enforced response schema."
    )


def _artifact_planner_user(
    *,
    question: str,
    task_contract: dict[str, Any],
    source_profile: dict[str, Any],
    prompt_context: dict[str, Any],
    selected_options: list[dict[str, Any]],
    discovery_only: bool,
    recovery_context: dict[str, Any] | None,
) -> str:
    return json.dumps(
        {
            "request": question,
            "task_contract": task_contract,
            "source_profile": source_profile,
            "selected_options": selected_options,
            "discovery_only": discovery_only,
            "rules": [
                "Discovery requests plan options only; the server will render decision_cards.",
                "Selected-option requests must plan exactly the selected options and no extras.",
                "Broad create requests must plan only artifacts you can build and validate.",
                "Use only generic parsing and aggregation concepts from the source profile.",
                "Every artifact must cite source ids from source_profile.sources.",
                "Return populated artifact choices; never return a schema object as the answer.",
            ],
            "recovery_context": recovery_context or {},
            "prompt_context": prompt_context,
        },
        ensure_ascii=False,
    )


def _artifact_plan_response_format() -> dict[str, Any]:
    artifact_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "description": "Stable id for this option or artifact."},
            "artifact_kind": {
                "type": "string",
                "enum": ["chart", "table", "summary_panel", "file_draft", "comparison", "mermaid"],
            },
            "chart_type": {"type": "string", "enum": ["", "bar", "line", "pie"]},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "source_columns": {"type": "array", "items": {"type": "string"}},
            "required_source_ids": {"type": "array", "items": {"type": "integer"}},
            "caveats": {"type": "array", "items": {"type": "string"}},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "id",
            "artifact_kind",
            "chart_type",
            "title",
            "description",
            "source_columns",
            "required_source_ids",
            "caveats",
            "acceptance_criteria",
        ],
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "filechat_artifact_plan",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mode": {"type": "string", "enum": ["discovery", "selected", "create"]},
                    "artifacts": {"type": "array", "minItems": 1, "items": artifact_schema},
                    "rationale": {"type": "string"},
                    "required_citations": {"type": "array", "items": {"type": "integer"}},
                    "caveats": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["mode", "artifacts", "rationale", "required_citations", "caveats", "acceptance_criteria"],
            },
        },
    }


def _artifact_planner_attempt(
    *,
    attempt_number: int,
    model: str,
    tactic: str,
    status: str,
    parsed: dict[str, Any] | None,
    error: str,
    response_class: str | None = None,
) -> dict[str, Any]:
    return {
        "attempt": attempt_number,
        "model": model,
        "tactic": tactic,
        "status": status,
        "response_format": "json_schema",
        "response_class": response_class or _artifact_plan_response_class(parsed),
        "response_keys": sorted(str(key) for key in parsed.keys()) if isinstance(parsed, dict) else [],
        "error": error,
    }


def _artifact_plan_response_class(parsed: dict[str, Any] | None) -> str:
    if not isinstance(parsed, dict):
        return "no_json"
    keys = {str(key) for key in parsed.keys()}
    if keys == {"schema"}:
        return "schema_only"
    plan = _find_artifact_plan(parsed)
    if not plan:
        return "no_plan"
    if not _coerce_plan_artifacts(plan):
        return "empty_plan"
    return "artifact_plan"


def _mark_artifact_planner_unhealthy(model: str, reason: str) -> None:
    if not model or reason == "artifact_plan":
        return
    _ARTIFACT_PLANNER_UNHEALTHY_UNTIL[model] = time.time() + ARTIFACT_PLANNER_HEALTH_TTL_SECONDS


def _mark_artifact_planner_success(model: str) -> None:
    global _ARTIFACT_PLANNER_LAST_SUCCESS
    if not model:
        return
    _ARTIFACT_PLANNER_UNHEALTHY_UNTIL.pop(model, None)
    _ARTIFACT_PLANNER_LAST_SUCCESS = (model, time.time() + ARTIFACT_PLANNER_HEALTH_TTL_SECONDS)


def _artifact_planner_model_healthy(model: str) -> bool:
    unhealthy_until = _ARTIFACT_PLANNER_UNHEALTHY_UNTIL.get(model)
    if unhealthy_until is None:
        return True
    if unhealthy_until <= time.time():
        _ARTIFACT_PLANNER_UNHEALTHY_UNTIL.pop(model, None)
        return True
    return False


def _artifact_planner_last_success_model() -> str | None:
    if not _ARTIFACT_PLANNER_LAST_SUCCESS:
        return None
    model, expires_at = _ARTIFACT_PLANNER_LAST_SUCCESS
    if expires_at <= time.time():
        return None
    return model


def _extract_artifact_plan(parsed: dict[str, Any], *, model: str) -> dict[str, Any]:
    plan = _find_artifact_plan(parsed)
    if not plan:
        keys = ", ".join(sorted(str(key) for key in parsed.keys())) if isinstance(parsed, dict) else ""
        detail = f" Response keys: {keys}." if keys else ""
        raise OpenRouterResponseError(f"Selected artifact planner `{model}` returned no artifact plan.{detail}")
    artifacts = _coerce_plan_artifacts(plan)
    if not artifacts:
        keys = ", ".join(sorted(str(key) for key in plan.keys()))
        raise OpenRouterResponseError(f"Selected artifact planner `{model}` returned no artifact plan. Plan keys: {keys}.")
    return {
        **plan,
        "artifacts": artifacts,
        "mode": str(plan.get("mode") or plan.get("intent") or "create"),
        "rationale": str(plan.get("rationale") or plan.get("reasoning") or plan.get("why") or ""),
        "required_citations": _coerce_int_list(plan.get("required_citations") or plan.get("source_ids")),
        "caveats": _coerce_string_list(plan.get("caveats") or plan.get("limitations")),
        "acceptance_criteria": _coerce_string_list(plan.get("acceptance_criteria") or plan.get("success_criteria")),
    }


def _find_artifact_plan(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("artifacts"), list):
        return value
    if any(isinstance(value.get(key), list) for key in _PLAN_ARTIFACT_LIST_KEYS):
        return value
    for key in ("artifact_plan", "plan", "result", "output", "response"):
        nested = value.get(key)
        found = _find_artifact_plan(nested)
        if found:
            return found
    return None


_PLAN_ARTIFACT_LIST_KEYS = (
    "artifacts",
    "artifact_options",
    "options",
    "decision_cards",
    "recommended_artifacts",
    "planned_artifacts",
    "possible_artifacts",
)


def _coerce_plan_artifacts(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw: Any = []
    for key in _PLAN_ARTIFACT_LIST_KEYS:
        if isinstance(plan.get(key), list):
            raw = plan[key]
            break
    artifacts: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        artifact_kind = str(
            item.get("artifact_kind")
            or item.get("kind")
            or item.get("type")
            or item.get("artifact_type")
            or item.get("output_type")
            or "summary_panel"
        ).strip()
        title = str(item.get("title") or item.get("label") or item.get("name") or f"Artifact {index}").strip()
        artifacts.append(
            {
                **item,
                "id": str(item.get("id") or item.get("option_id") or f"artifact_{index}").strip(),
                "artifact_kind": artifact_kind,
                "chart_type": str(item.get("chart_type") or item.get("visualization_type") or "").strip(),
                "title": title,
                "description": str(item.get("description") or item.get("rationale") or item.get("reason") or "").strip(),
                "source_columns": _coerce_string_list(item.get("source_columns") or item.get("columns")),
                "required_source_ids": _coerce_int_list(item.get("required_source_ids") or item.get("source_ids")),
                "caveats": _coerce_string_list(item.get("caveats") or item.get("limitations")),
                "acceptance_criteria": _coerce_string_list(item.get("acceptance_criteria") or item.get("success_criteria")),
            }
        )
    return artifacts


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _fake_artifact_plan(
    *,
    question: str,
    task_contract: dict[str, Any],
    source_profile: dict[str, Any],
    selected_options: list[dict[str, Any]],
    discovery_only: bool,
) -> dict[str, Any]:
    if selected_options:
        return {
            "mode": "selected",
            "artifacts": [
                {
                    "id": option["id"],
                    "artifact_kind": option.get("artifact_kind") or "summary_panel",
                    "chart_type": option.get("chart_type") or "",
                    "title": option.get("label") or option["id"],
                    "description": option.get("description") or "Selected source-grounded artifact.",
                    "required_source_ids": _profile_source_ids(source_profile),
                    "caveats": [],
                    "acceptance_criteria": ["Produce exactly this selected artifact."],
                }
                for option in selected_options
            ],
            "rationale": "User selected server-owned artifact options.",
            "required_citations": _profile_source_ids(source_profile),
            "caveats": [],
            "acceptance_criteria": ["No extra artifacts."],
        }
    table = _first_profile_table(source_profile)
    columns = table.get("columns", []) if table else []
    numeric_columns = [column for column in columns if column.get("role") == "numeric"]
    normalized = question.lower()
    numeric = (
        next((column for column in numeric_columns if "forecast" in str(column.get("name") or "").lower()), None)
        if "forecast" in normalized or "best chart" in normalized
        else None
    ) or (numeric_columns[0] if numeric_columns else None)
    date_like = next((column for column in columns if column.get("role") == "date_like"), None)
    category = next((column for column in columns if column.get("role") == "categorical"), None)
    text_column = next((column for column in columns if column.get("role") == "text"), None)
    requested_outputs = list(
        dict.fromkeys(
            str(output)
            for output in [
                *(task_contract.get("required_outputs") or []),
                *(task_contract.get("primary_outputs") or []),
                *(task_contract.get("supporting_outputs") or []),
            ]
            if str(output) in {"chart", "table", "summary_panel", "file_draft", "comparison", "mermaid"}
        )
    )
    supporting_outputs = {str(output) for output in (task_contract.get("supporting_outputs") or [])}
    chart_type = "line" if date_like and numeric and ("line" in normalized or "trend" in normalized or "over time" in normalized or "best chart" in normalized) else "bar"
    artifacts: list[dict[str, Any]] = []
    should_plan_chart = "chart" in requested_outputs or (not requested_outputs and (numeric or category))
    should_plan_table = "table" in requested_outputs or (discovery_only and bool(table))
    should_plan_draft = "file_draft" in requested_outputs or any(term in normalized for term in ("draft", "report", "document", "new file")) or any(term in question for term in ("자료", "보고서", "문서", "작성", "제작"))
    should_plan_summary = "summary_panel" in requested_outputs
    chart_label_column = date_like or category or text_column or (columns[0] if columns else {})
    if should_plan_chart and (numeric or category or text_column or date_like):
        artifacts.append(
            {
                "id": "chart_1",
                "artifact_kind": "chart",
                "chart_type": chart_type,
                "title": _generic_title(table, "chart") if table else "Source chart",
                "description": "Aggregate source rows into a chart; segment-level variation may need follow-up review.",
                "source_columns": [
                    str(chart_label_column.get("name") or "Category"),
                    str((numeric or {}).get("name") or "Count"),
                ],
                "required_source_ids": _profile_source_ids(source_profile),
                "caveats": ["Aggregations use the attached source rows."],
                "acceptance_criteria": ["Chart values cite source chunks."],
            }
        )
    if should_plan_table:
        artifacts.append(
            {
                "id": "table_1",
                "artifact_kind": "table",
                "title": _generic_title(table, "table") if table else "Source table",
            "description": "Create a compact table from profiled source rows; segment-level variation remains visible in rows.",
                "source_columns": [str(column.get("name")) for column in columns[:6] if column.get("name")],
                "required_source_ids": _profile_source_ids(source_profile),
                "caveats": ["Preview rows are limited."],
                "acceptance_criteria": ["Table cites source chunks."],
            }
        )
    if should_plan_draft:
        artifacts.append(
            {
                "id": "draft_1",
                "artifact_kind": "file_draft",
                "title": _generic_title(table, "draft") if table else "Source draft",
                "description": "Write a concise Markdown draft grounded in source profile facts.",
                "source_columns": [str(column.get("name")) for column in columns[:6] if column.get("name")],
                "required_source_ids": _profile_source_ids(source_profile),
                "caveats": ["The draft should cite the source profile and avoid unsupported claims."],
                "acceptance_criteria": ["Draft content is not a raw dump."],
            }
        )
    if should_plan_summary:
        artifacts.append(
            {
                "id": "summary_1",
                "artifact_kind": "summary_panel",
                "title": _generic_title(table, "summary") if table else "Source summary",
                "description": "Summarize the profiled source.",
                "required_source_ids": _profile_source_ids(source_profile),
                "caveats": [],
                "acceptance_criteria": ["Summary cites source chunks."],
                "display_mode": "supporting" if "summary_panel" in supporting_outputs else "primary",
            }
        )
    if not artifacts:
        artifacts.append(
            {
                "id": "summary_1",
                "artifact_kind": "summary_panel",
                "title": "Source summary",
                "description": "Summarize the profiled source.",
                "required_source_ids": _profile_source_ids(source_profile),
                "caveats": [],
                "acceptance_criteria": ["Summary cites source chunks."],
            }
        )
    return {
        "mode": "discovery" if discovery_only else "create",
        "artifacts": artifacts[:4],
        "rationale": str(source_profile.get("summary") or "Artifacts are based on the source profile."),
        "required_citations": _profile_source_ids(source_profile),
        "caveats": [],
        "acceptance_criteria": ["Artifacts must validate before persistence."],
    }


def _fake_build_artifacts(
    *,
    question: str,
    artifact_plan: dict[str, Any],
    source_profile: dict[str, Any],
    sources: list[dict[str, Any]],
    model: str,
) -> ChatResult:
    table = _first_profile_table(source_profile)
    artifacts = []
    for item in artifact_plan.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("artifact_kind") or "summary_panel")
        if kind == "chart":
            artifacts.append(_fake_chart(item, table, sources))
        elif kind == "table":
            artifacts.append(_fake_table(item, table, sources))
        elif kind == "file_draft":
            artifacts.append(_fake_file_draft(item, table, source_profile, sources, question=question))
        elif kind == "summary_panel":
            artifacts.append(_fake_summary_panel(item, table, source_profile, sources))
        elif kind == "mermaid":
            artifacts.append(_fake_mermaid(item, source_profile, sources))
        else:
            artifacts.append(_fake_summary_panel(item, table, source_profile, sources))
    cited = _profile_source_ids(source_profile) or ([int(sources[0]["source_id"])] if sources else [])
    return ChatResult(
        answer=f"I created {len(artifacts)} source-grounded artifact{'s' if len(artifacts) != 1 else ''}: "
        + ", ".join(str(artifact.get("title") or artifact.get("kind")) for artifact in artifacts)
        + ".",
        cited_source_ids=cited,
        artifacts=artifacts,
        model=model,
        usage=UsageInfo(),
    )


def _fake_chart(plan_item: dict[str, Any], table: dict[str, Any] | None, sources: list[dict[str, Any]]) -> dict[str, Any]:
    columns = table.get("columns", []) if table else []
    rows = table.get("sample_rows", []) if table else []
    planned_columns = [str(column) for column in plan_item.get("source_columns", [])] if isinstance(plan_item.get("source_columns"), list) else []
    numeric_columns = [column for column in columns if column.get("role") == "numeric"]
    numeric = (
        next((column for column in numeric_columns if str(column.get("name") or "") == planned_columns[1]), None)
        if len(planned_columns) > 1
        else None
    ) or (numeric_columns[0] if numeric_columns else None)
    date_like = next((column for column in columns if column.get("role") == "date_like"), None)
    category = next((column for column in columns if column.get("role") == "categorical"), None)
    text_column = next((column for column in columns if column.get("role") == "text"), None)
    label_source = date_like if plan_item.get("chart_type") == "line" else category or text_column or date_like or (columns[0] if columns else {})
    label_column = str(label_source.get("name") or "Label")
    value_column = str((numeric or {}).get("name") or "Count")
    grouped: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get(label_column) or "Source").strip()
        value = _payload_number(row.get(value_column)) if numeric else 1.0
        if label and value is not None:
            grouped[label] = grouped.get(label, 0.0) + value
    values = [
        {"label": label, "value": value, "source_id": _source_id(sources)}
        for label, value in grouped.items()
    ] or [{"label": "Rows", "value": float(table.get("row_count") or 1) if table else 1.0, "source_id": _source_id(sources)}]
    source_columns = [label_column, value_column]
    return {
        "kind": "chart",
        "title": str(plan_item.get("title") or "Source chart"),
        "caption": str(plan_item.get("description") or "Source-grounded chart."),
        "source_ids": [_source_id(sources)],
        "chart_type": str(plan_item.get("chart_type") or "bar") if str(plan_item.get("chart_type") or "bar") in {"bar", "line", "pie"} else "bar",
        "x_label": label_column,
        "y_label": value_column,
        "x_column": label_column,
        "y_column": value_column,
        "source_columns": source_columns,
        "values": values[:12],
        "insight_narrative": {
            "headline": str(plan_item.get("title") or "Source chart"),
            "meaning": f"The chart compares {label_column} against {value_column} from the cited source rows.",
            "evidence": [
                f"x-axis is {label_column}.",
                f"measure is {value_column}.",
            ],
            "so_what": "Use this chart to decide which source segment needs the next review.",
            "recommended_actions": ["Inspect the largest segment before acting."],
            "follow_up_questions": [
                {
                    "id": "segment_review",
                    "group": "data",
                    "question": f"Which {label_column} segment should be reviewed next?",
                    "options": [
                        {"id": "largest", "label": "Largest segment"},
                        {"id": "smallest", "label": "Smallest segment"},
                    ],
                    "default_option": "largest",
                    "requires_reference": True,
                }
            ],
            "caveats": ["Aggregated values can hide row-level variation."],
            "confidence": "medium",
            "source_columns": source_columns,
        },
    }


def _fake_table(plan_item: dict[str, Any], table: dict[str, Any] | None, sources: list[dict[str, Any]]) -> dict[str, Any]:
    columns = [str(column.get("name")) for column in (table.get("columns", []) if table else [])[:6] if column.get("name")] or ["Field", "Value"]
    sample_rows = table.get("sample_rows", []) if table else []
    rows = [[str(row.get(column, "")) for column in columns] for row in sample_rows[:12] if isinstance(row, dict)] or [["Rows", str(table.get("row_count") if table else 0)]]
    return {
        "kind": "table",
        "title": str(plan_item.get("title") or "Source table"),
        "caption": str(plan_item.get("description") or "Source-grounded table."),
        "source_ids": [_source_id(sources)],
        "columns": columns,
        "rows": rows,
    }


def _fake_file_draft(
    plan_item: dict[str, Any],
    table: dict[str, Any] | None,
    source_profile: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    question: str = "",
) -> dict[str, Any]:
    title = str(plan_item.get("title") or "Source draft")
    lines = [
        f"# {title}",
        "",
        "## Source Profile",
        f"- {source_profile.get('summary') or 'Source profile prepared.'}",
    ]
    if table:
        lines.extend(
            [
                f"- Rows: {table.get('row_count')}",
                f"- Columns: {', '.join(str(column.get('name')) for column in table.get('columns', [])[:8])}",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "- This draft uses the profiled columns, row counts, sample values, and cited chunks as its factual boundary.",
            "- The strongest values or most frequent categories should be treated as review priorities, not as causal proof.",
            "- Any decision-ready version should add owner context, thresholds, and missing source fields before action.",
            "",
            "## Suggested Next Checks",
            "- Compare the highest-value rows against the original source file.",
            "- Confirm whether missing values or short samples change the interpretation.",
            "- Keep the cited source chunk attached when sharing the draft.",
            "",
            "## Caveats",
            "- Uses only cited source profile facts and limited samples.",
        ]
    )
    filename = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "source-draft"
    artifact = {
        "kind": "file_draft",
        "title": title,
        "caption": str(plan_item.get("description") or "Source-grounded draft."),
        "source_ids": [_source_id(sources)],
        "filename": f"{filename}.md",
        "format": "markdown",
        "content": "\n".join(lines),
    }
    normalized_question = f"{question} {title} {plan_item.get('description') or ''}".lower()
    if "open design" in normalized_question or "design brief" in normalized_question or "material" in normalized_question:
        material_type = "design_brief" if "brief" in normalized_question else "docs_page"
        if "report" in normalized_question:
            material_type = "report"
        elif "one pager" in normalized_question or "one-pager" in normalized_question:
            material_type = "one_pager"
        elif "pack" in normalized_question:
            material_type = "material_pack"
        artifact["open_design"] = {"material_type": material_type, "skill_name": title}
    return artifact


def _fake_summary_panel(plan_item: dict[str, Any], table: dict[str, Any] | None, source_profile: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    sections = [{"heading": "Profile", "body": str(source_profile.get("summary") or "Source profile prepared.")}]
    if table:
        sections.append({"heading": "Table", "body": f"{table.get('row_count')} rows across {table.get('column_count')} columns."})
    return {
        "kind": "summary_panel",
        "title": str(plan_item.get("title") or "Source summary"),
        "caption": str(plan_item.get("description") or "Source-grounded summary."),
        "display_mode": str(plan_item.get("display_mode") or "primary") if str(plan_item.get("display_mode") or "primary") in {"primary", "supporting"} else "primary",
        "source_ids": [_source_id(sources)],
        "sections": sections,
    }


def _fake_mermaid(plan_item: dict[str, Any], source_profile: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "mermaid",
        "title": str(plan_item.get("title") or "Source flow"),
        "caption": str(plan_item.get("description") or "Source-grounded flow."),
        "source_ids": [_source_id(sources)],
        "diagram": "flowchart TD\n  Source[Source profile] --> Plan[Artifact plan]\n  Plan --> Build[Validated artifact]",
    }


def _first_profile_table(source_profile: dict[str, Any]) -> dict[str, Any] | None:
    tables = source_profile.get("tables") if isinstance(source_profile.get("tables"), list) else []
    return tables[0] if tables and isinstance(tables[0], dict) else None


def _profile_source_ids(source_profile: dict[str, Any]) -> list[int]:
    sources = source_profile.get("sources") if isinstance(source_profile.get("sources"), list) else []
    out = [int(source.get("source_id")) for source in sources if isinstance(source, dict) and str(source.get("source_id")).isdigit()]
    return out[:4]


def _source_id(sources: list[dict[str, Any]]) -> int:
    return int(sources[0]["source_id"]) if sources and sources[0].get("source_id") is not None else 1


def _generic_title(table: dict[str, Any] | None, suffix: str) -> str:
    if not table:
        return f"Source {suffix}"
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", str(table.get("file_name") or "source"))
    stem = re.sub(r"[_-]+", " ", stem).strip() or "Source"
    return f"{stem[:1].upper() + stem[1:]} {suffix}"


def _payload_number(value: Any) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if cleaned in {"", ".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _fake_chart_values(source: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line in str(source.get("content") or "").splitlines():
        parts = [part.strip() for part in re.split(r",|:|\t", line) if part.strip()]
        if len(parts) < 2:
            continue
        label = parts[0]
        number = None
        for part in reversed(parts[1:]):
            try:
                number = float(re.sub(r"[^0-9.\-]", "", part))
                break
            except ValueError:
                continue
        if number is None:
            continue
        values.append({"label": label, "value": number, "source_id": source["source_id"]})
        if len(values) >= 8:
            break
    return values or [
        {"label": "North", "value": 1470, "source_id": source["source_id"]},
        {"label": "South", "value": 840, "source_id": source["source_id"]},
    ]


def _fallback_draft_content(evidence_packet: dict[str, Any]) -> str:
    title = str(evidence_packet.get("recommended_title") or "Survey analysis draft")
    dataset = evidence_packet.get("dataset") if isinstance(evidence_packet.get("dataset"), dict) else {}
    themes = evidence_packet.get("theme_counts") if isinstance(evidence_packet.get("theme_counts"), list) else []
    survey_context = bool(dataset.get("survey_context"))
    subject = "응답" if survey_context else "행"
    lines = [f"# {title}", "", "## 핵심 요약", f"- 총 {dataset.get('row_count', 'N/A')}건의 {subject}을 첨부 원자료 기준으로 정리했습니다."]
    for item in themes[:4]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('label')}: {item.get('value')}건")
    if survey_context:
        lines.extend(
            [
                "",
                "## 해석",
                "- 반복 빈도가 높은 주제는 개인의 숙련도 문제보다 업무 흐름과 검수 체계의 병목일 가능성이 큽니다.",
                "- 일정/커뮤니케이션과 플랫폼 업로드 이슈는 개인 생산성보다 프로세스 병목에 가깝습니다.",
                "- AI 활용은 기대와 검증 부담이 함께 나타나므로, 자동화 후보와 검수 기준을 함께 설계해야 합니다.",
                "",
                "## 권장 액션",
                "- 상위 주제별 대표 응답을 2~3개씩 골라 병목 업무 정의서를 만듭니다.",
                "- 반복 검토/교정 업무부터 템플릿, 체크리스트, 자동화 가능성을 분리해 실험합니다.",
                "- 리더 공유 자료에는 주제별 빈도, 대표 사례, 다음 실험을 한 장으로 묶는 구성이 적합합니다.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## 해석",
                "- 같은 키로 연결되는 열을 우선 비교하면 실행 가능한 판단 후보를 만들 수 있습니다.",
                "- 기준값과 실제값의 차이가 큰 항목은 후속 검토 후보입니다.",
                "- 이 초안은 첨부 원자료의 행과 열에서 확인 가능한 값만 사용합니다.",
                "",
                "## 권장 액션",
                "- 공통 키로 다른 첨부 파일의 관련 데이터를 교차 확인합니다.",
                "- 기준을 벗어나거나 맥락이 부족한 항목을 우선순위 후보로 표시합니다.",
                "- Notion 공유용 문서에는 원자료 표와 차트, 후속 확인 질문을 함께 남깁니다.",
            ]
        )
    return "\n".join(lines)
