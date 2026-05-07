from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings
from .smoke_runner import create_correlated_business_fixtures


LIVE_FALLBACK_MODELS = [
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash",
    "anthropic/claude-sonnet-4.5",
]


class LiveArtifactAutonomyProbe:
    def __init__(
        self,
        *,
        base_url: str,
        work_dir: Path,
        timeout_seconds: float = 240.0,
        fallback_models: list[str] | None = None,
        web_url: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.work_dir = work_dir
        self.timeout_seconds = timeout_seconds
        self.fallback_models = fallback_models or LIVE_FALLBACK_MODELS
        self.web_url = web_url.rstrip("/") if web_url else None

    def run(self) -> dict[str, Any]:
        if get_settings().filechat_allow_fake_openrouter:
            raise RuntimeError("Live artifact probe refuses to run with FILECHAT_ALLOW_FAKE_OPENROUTER=true.")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(timezone.utc).isoformat()
        report: dict[str, Any] = {
            "status": "running",
            "started_at": started_at,
            "base_url": self.base_url,
            "fallback_models": self.fallback_models,
            "flows": [],
            "direct_model_checks": [],
            "frontend_render_check": {"status": "not_run"},
        }
        with httpx.Client(timeout=120) as client:
            settings = self._get_settings(client)
            self._assert_live_provider(settings)
            report["initial_settings"] = _settings_snapshot(settings)
            try:
                try:
                    report["flows"].append(self._run_warehouse_discovery_and_selection(client))
                except Exception as exc:
                    report["flows"].append({"name": "warehouse_discovery_and_selection", "status": "failed", "error": str(exc)})
                for model in self.fallback_models:
                    try:
                        report["direct_model_checks"].append(self._run_direct_model_discovery(client, model))
                    except Exception as exc:
                        report["direct_model_checks"].append({"model": model, "status": "failed", "error": str(exc)})
                try:
                    report["frontend_render_check"] = self._frontend_check(client)
                except Exception as exc:
                    report["frontend_render_check"] = {"status": "failed", "error": str(exc)}
            finally:
                self._restore_settings(client, settings)
        failures = [
            item
            for group in ("flows", "direct_model_checks")
            for item in report.get(group, [])
            if item.get("status") != "passed"
        ]
        if report.get("frontend_render_check", {}).get("status") == "failed":
            failures.append(report["frontend_render_check"])
        report["status"] = "failed" if failures else "passed"
        report["failures"] = failures
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        return report

    def _get_settings(self, client: httpx.Client) -> dict[str, Any]:
        response = client.get(f"{self.base_url}/api/settings")
        response.raise_for_status()
        return response.json()

    def _assert_live_provider(self, settings: dict[str, Any]) -> None:
        if not settings.get("openrouter_key_configured"):
            raise RuntimeError("OpenRouter key is not configured.")
        if settings.get("openrouter_provider_status") != "verified":
            raise RuntimeError(f"OpenRouter provider is not verified: {settings.get('openrouter_provider_message')}")
        message = str(settings.get("openrouter_provider_message") or "").lower()
        if "fake openrouter" in message:
            raise RuntimeError("Live artifact probe refuses to run against fake OpenRouter mode.")

    def _patch_settings(self, client: httpx.Client, patch: dict[str, Any]) -> None:
        response = client.patch(f"{self.base_url}/api/settings", json=patch)
        response.raise_for_status()

    def _restore_settings(self, client: httpx.Client, settings: dict[str, Any]) -> None:
        patch = {
            "chat_model": settings.get("chat_model"),
            "orchestrator_model": settings.get("orchestrator_model"),
            "writing_model": settings.get("writing_model"),
            "repair_model": settings.get("repair_model"),
            "reasoning_effort": settings.get("reasoning_effort"),
            "model_routing_mode": settings.get("model_routing_mode"),
        }
        self._patch_settings(client, {key: value for key, value in patch.items() if value is not None})

    def _run_warehouse_discovery_and_selection(self, client: httpx.Client) -> dict[str, Any]:
        self._patch_settings(
            client,
            {
                "orchestrator_model": "deepseek/deepseek-v4-pro",
                "writing_model": "deepseek/deepseek-v4-pro",
                "repair_model": "deepseek/deepseek-v4-pro",
                "reasoning_effort": "medium",
            },
        )
        fixture_dir = self.work_dir / "correlated_business"
        fixture_paths = create_correlated_business_fixtures(fixture_dir)
        warehouse = next(path for path in fixture_paths if path.name == "warehouse_stock_units.csv")
        session = self._create_session(client, "Live artifact autonomy - warehouse")
        self._upload(client, session["id"], warehouse)
        run = self._start_and_wait_run(client, session["id"], "what charts can we make from this file?")
        workspace = self._workspace(client, session["id"], run["id"])
        attempts = _planner_attempts(workspace)
        discovery_message = self._latest_message(client, session["id"])
        flow: dict[str, Any] = {
            "name": "warehouse_discovery_and_selection",
            "status": "passed",
            "session_id": session["id"],
            "discovery_run_id": run["id"],
            "discovery_status": run["status"],
            "planner_attempts": attempts,
            "final_planner_model": _final_planner_model(attempts),
            "discovery_artifact_kinds": [artifact["kind"] for artifact in discovery_message.get("artifacts", [])],
            "citation_count": len(discovery_message.get("citations", [])),
        }
        if run["status"] != "completed":
            return _fail(flow, f"Discovery run ended as {run['status']}: {run.get('error')}")
        if not any(artifact.get("kind") == "decision_cards" for artifact in discovery_message.get("artifacts", [])):
            return _fail(flow, "Discovery did not produce decision cards.")
        question = next((item for item in run.get("follow_up_questions", []) if item.get("kind") == "artifact_choice"), None)
        if not question:
            return _fail(flow, "Discovery run did not create an artifact-choice follow-up.")
        option_ids = [str(option.get("id")) for option in question.get("card", {}).get("options", []) if option.get("id")][:2]
        if len(option_ids) < 2:
            return _fail(flow, "Artifact-choice follow-up did not expose at least two server options.")
        child = self._answer_choice_and_wait(client, session["id"], run["id"], question["id"], option_ids)
        child_message = self._latest_message(client, session["id"])
        flow.update(
            {
                "child_run_id": child["id"],
                "child_status": child["status"],
                "selected_options": option_ids,
                "child_artifact_kinds": [artifact["kind"] for artifact in child_message.get("artifacts", [])],
                "child_citation_count": len(child_message.get("citations", [])),
            }
        )
        if child["status"] != "completed":
            return _fail(flow, f"Selected artifact child run ended as {child['status']}: {child.get('error')}")
        if len(child_message.get("artifacts", [])) != 2:
            return _fail(flow, "Selected artifact child run did not produce exactly two artifacts.")
        return flow

    def _run_direct_model_discovery(self, client: httpx.Client, model: str) -> dict[str, Any]:
        self._patch_settings(client, {"orchestrator_model": model, "reasoning_effort": "medium"})
        neutral = self.work_dir / f"neutral-{model.replace('/', '_')}.csv"
        neutral.write_text("Stage,Amount,Owner\nIntake,10,Ada\nReview,15,Byron\nReview,5,Ada\nDone,20,Cy\n", encoding="utf-8")
        session = self._create_session(client, f"Live artifact autonomy - {model}")
        self._upload(client, session["id"], neutral)
        run = self._start_and_wait_run(client, session["id"], "what charts can we make from this file?")
        message = self._latest_message(client, session["id"])
        workspace = self._workspace(client, session["id"], run["id"])
        attempts = _planner_attempts(workspace)
        result = {
            "model": model,
            "status": "passed",
            "session_id": session["id"],
            "run_id": run["id"],
            "run_status": run["status"],
            "planner_attempts": attempts,
            "final_planner_model": _final_planner_model(attempts),
            "artifact_kinds": [artifact["kind"] for artifact in message.get("artifacts", [])],
            "citation_count": len(message.get("citations", [])),
        }
        if run["status"] != "completed":
            return _fail(result, f"Run ended as {run['status']}: {run.get('error')}")
        if not any(artifact.get("kind") == "decision_cards" for artifact in message.get("artifacts", [])):
            return _fail(result, "Model did not produce decision cards.")
        serialized = json.dumps(message.get("artifacts", []), ensure_ascii=False).lower()
        for stale in ("ai adoption", "groupware", "sales orders"):
            if stale in serialized:
                return _fail(result, f"Stale domain label found: {stale}")
        return result

    def _create_session(self, client: httpx.Client, title: str) -> dict[str, Any]:
        response = client.post(f"{self.base_url}/api/sessions", json={"title": title})
        response.raise_for_status()
        return response.json()

    def _upload(self, client: httpx.Client, session_id: str, path: Path) -> list[dict[str, Any]]:
        with path.open("rb") as handle:
            response = client.post(
                f"{self.base_url}/api/sessions/{session_id}/files",
                files={"uploads": (path.name, handle, "text/csv" if path.suffix == ".csv" else "text/plain")},
            )
        response.raise_for_status()
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            files = client.get(f"{self.base_url}/api/sessions/{session_id}/files")
            files.raise_for_status()
            payload = files.json()
            if payload and all(file.get("status") == "ready" for file in payload):
                return payload
            if any(file.get("status") == "failed" for file in payload):
                raise RuntimeError(f"File processing failed: {payload}")
            time.sleep(0.75)
        raise TimeoutError("Timed out waiting for files to become ready.")

    def _start_and_wait_run(self, client: httpx.Client, session_id: str, prompt: str) -> dict[str, Any]:
        response = client.post(f"{self.base_url}/api/sessions/{session_id}/runs", json={"content": prompt})
        response.raise_for_status()
        run_id = response.json()["id"]
        return self._wait_run(client, session_id, run_id)

    def _answer_choice_and_wait(self, client: httpx.Client, session_id: str, run_id: str, question_id: str, option_ids: list[str]) -> dict[str, Any]:
        response = client.post(
            f"{self.base_url}/api/sessions/{session_id}/runs/{run_id}/questions/{question_id}/answer",
            json={"answer": {"selected_options": option_ids}},
        )
        response.raise_for_status()
        child_id = response.json()["id"]
        return self._wait_run(client, session_id, child_id)

    def _wait_run(self, client: httpx.Client, session_id: str, run_id: str) -> dict[str, Any]:
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            response = client.get(f"{self.base_url}/api/sessions/{session_id}/runs/{run_id}")
            response.raise_for_status()
            run = response.json()
            if run.get("status") not in {"queued", "running"}:
                return run
            time.sleep(1.0)
        raise TimeoutError(f"Timed out waiting for run {run_id}.")

    def _workspace(self, client: httpx.Client, session_id: str, run_id: str) -> list[dict[str, Any]]:
        response = client.get(f"{self.base_url}/api/sessions/{session_id}/runs/{run_id}/workspace")
        response.raise_for_status()
        return response.json()

    def _latest_message(self, client: httpx.Client, session_id: str) -> dict[str, Any]:
        response = client.get(f"{self.base_url}/api/sessions/{session_id}/messages")
        response.raise_for_status()
        messages = response.json()
        return messages[-1] if messages else {}

    def _frontend_check(self, client: httpx.Client) -> dict[str, Any]:
        if not self.web_url:
            return {"status": "not_run", "reason": "No web URL was provided."}
        response = client.get(self.web_url)
        if response.status_code >= 400:
            return {"status": "failed", "web_url": self.web_url, "http_status": response.status_code}
        if "FileChat" not in response.text:
            return {"status": "failed", "web_url": self.web_url, "http_status": response.status_code, "reason": "FileChat shell not found."}
        return {"status": "passed", "web_url": self.web_url, "http_status": response.status_code}


def write_live_report(report: dict[str, Any], export_path: Path | None = None) -> Path:
    if export_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        export_path = Path("reports") / f"live-artifact-autonomy-{stamp}.json"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return export_path


def _settings_snapshot(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: settings.get(key)
        for key in (
            "openrouter_key_source",
            "openrouter_provider_status",
            "chat_model",
            "orchestrator_model",
            "writing_model",
            "repair_model",
            "reasoning_effort",
            "model_routing_mode",
        )
    }


def _planner_attempts(workspace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in workspace:
        if item.get("path") == "/review/artifact-planner-attempts.json":
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            attempts = content.get("attempts")
            return attempts if isinstance(attempts, list) else []
    return []


def _final_planner_model(attempts: list[dict[str, Any]]) -> str:
    for attempt in reversed(attempts):
        if attempt.get("status") == "passed":
            return str(attempt.get("model") or "")
    return ""


def _fail(item: dict[str, Any], error: str) -> dict[str, Any]:
    item["status"] = "failed"
    item["error"] = error
    return item
