from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import get_settings
from .prompt_context import prompt_pack
from .settings_store import get_openrouter_key
from .usage import UsageInfo, usage_from_response

OPENROUTER_URL = "https://openrouter.ai/api/v1"


class OpenRouterMissingKey(RuntimeError):
    pass


class OpenRouterResponseError(RuntimeError):
    pass


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
                    "Charts require chart_type, x_label, y_label, values [{label,value,source_id}]. File drafts require filename, format, content."
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

    def _fake_artifacts(self, question: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = question.lower()
        source = sources[0]
        if any(word in normalized for word in ("chart", "graph", "plot")):
            values: list[dict[str, Any]] = []
            for line in source["content"].splitlines():
                parts = [part.strip() for part in re.split(r",|:|\t", line) if part.strip()]
                if len(parts) < 2:
                    continue
                try:
                    number = float(re.sub(r"[^0-9.\-]", "", parts[-1]))
                except ValueError:
                    continue
                values.append({"label": parts[0], "value": number, "source_id": source["source_id"]})
                if len(values) >= 8:
                    break
            if values:
                return [
                    {
                        "kind": "chart",
                        "title": "Survey chart",
                        "caption": "Generated from retrieved source rows.",
                        "source_ids": [source["source_id"]],
                        "chart_type": "bar",
                        "x_label": "Response",
                        "y_label": "Count",
                        "values": values,
                    }
                ]
        if any(phrase in normalized for phrase in ("new file", "draft", "write a file", "create a file")):
            return [
                {
                    "kind": "file_draft",
                    "title": "Grounded draft",
                    "caption": "Drafted from the attached source.",
                    "source_ids": [source["source_id"]],
                    "filename": "grounded-draft.md",
                    "format": "markdown",
                    "content": f"# Grounded Draft\n\n{source['excerpt']}",
                }
            ]
        return []


def _fallback_draft_content(evidence_packet: dict[str, Any]) -> str:
    title = str(evidence_packet.get("recommended_title") or "Survey analysis draft")
    dataset = evidence_packet.get("dataset") if isinstance(evidence_packet.get("dataset"), dict) else {}
    themes = evidence_packet.get("theme_counts") if isinstance(evidence_packet.get("theme_counts"), list) else []
    lines = [
        f"# {title}",
        "",
        "## 핵심 요약",
        f"- 총 {dataset.get('row_count', 'N/A')}건의 응답에서 반복 업무와 병목 주제를 정리했습니다.",
    ]
    for item in themes[:4]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('label')}: {item.get('value')}건")
    lines.extend(
        [
            "",
            "## 해석",
            "- 가장 큰 신호는 반복 검토와 교정 업무가 실제 작업 시간을 크게 잠식한다는 점입니다.",
            "- 일정/커뮤니케이션과 플랫폼 업로드 이슈는 개인 생산성보다 프로세스 병목에 가깝습니다.",
            "- AI 활용은 기대와 검증 부담이 함께 나타나므로, 자동화 후보와 검수 기준을 함께 설계해야 합니다.",
            "",
            "## 권장 액션",
            "- 상위 주제별 대표 응답을 2~3개씩 골라 병목 업무 정의서를 만듭니다.",
            "- 반복 검토/교정 업무부터 템플릿, 체크리스트, 자동화 가능성을 분리해 실험합니다.",
            "- 리더 공유 자료에는 주제별 빈도, 대표 사례, 다음 실험을 한 장으로 묶는 구성이 적합합니다.",
        ]
    )
    return "\n".join(lines)
