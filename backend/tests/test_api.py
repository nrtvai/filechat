import hashlib
import hmac
import io
import json
import time
import zipfile
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient

from backend.app.audit import record_audit_event
from backend.app.auth import Principal
from backend.app.config import get_settings
from backend.app.agent_runs import create_agent_run, create_run_question, get_agent_run, set_action
from backend.app.agent_runtime import review_contract_result
from backend.app.analysis_engine import build_insight_brief
from backend.app.artifacts import ValidatedArtifact, validate_artifacts_with_report
from backend.app.database import connect
from backend.app.main import app
from backend.app.openrouter import ChatResult, EmbeddingResult, OpenRouterClient, OpenRouterResponseError
from backend.app.providers import DEFAULT_PROVIDER_ID, provider_registry
from backend.app.retrieval import _replace_draft_artifact
from backend.app.settings_store import get_openrouter_key, set_setting
from backend.app.usage import UsageInfo
from backend.app.utils import now


def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "true")
    get_settings.cache_clear()
    return TestClient(app)


def openrouter_401() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/embeddings")
    response = httpx.Response(401, request=request)
    return httpx.HTTPStatusError(
        "Client error '401 Unauthorized' for url 'https://openrouter.ai/api/v1/embeddings'",
        request=request,
        response=response,
    )


def slack_headers(secret: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    base = b"v0:" + timestamp.encode("utf-8") + b":" + body
    signature = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature}


def answer_current_question(client: TestClient, session_id: str, run: dict, selected_option: str, free_text: str = ""):
    return client.post(
        f"/api/sessions/{session_id}/runs/{run['id']}/questions/{run['current_question']['id']}/answer",
        json={"selected_option": selected_option, "free_text": free_text},
    )


def test_upload_text_file_reaches_ready_and_answers(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Annual report"}).json()
        upload = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("report.txt", b"North America revenue rose because acquisition revenue expanded.", "text/plain")},
        )
        assert upload.status_code == 200

        files = client.get(f"/api/sessions/{session['id']}/files").json()
        assert files[0]["status"] == "ready"
        assert files[0]["chunk_count"] >= 1

        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "Why did North America revenue rise?"},
        )
        assert answer.status_code == 200
        payload = answer.json()
        assert payload["role"] == "assistant"
        assert payload["citations"]


def test_filechat_original_smoke_returns_grounded_answer_with_provenance(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Grounded FileChat smoke"}).json()
        upload = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("warehouse_memo.txt", b"Warehouse Alpha has 42 packed orders ready for dispatch.", "text/plain")},
        )
        assert upload.status_code == 200

        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "How many packed orders does Warehouse Alpha have ready for dispatch?"},
        )

        assert answer.status_code == 200
        payload = answer.json()
        assert "42" in payload["content"]
        assert payload["citations"]
        assert payload["grounding"]["status"] == "cited"
        citation = payload["citations"][0]
        assert citation["source_label"] == "warehouse_memo.txt"
        assert citation["location"] == "chunk 1"
        assert "Warehouse Alpha" in citation["excerpt"]


def test_excel_workflow_smoke_reconciles_multiple_csvs_with_row_provenance(monkeypatch, tmp_path):
    async def fail_provider_setup():
        raise AssertionError("Excel Workflow Automation should not require provider setup")

    monkeypatch.setattr("backend.app.retrieval.ensure_provider_ready", fail_provider_setup)
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Excel Workflow smoke"}).json()
        upload = client.post(
            f"/api/sessions/{session['id']}/files",
            files=[
                ("uploads", ("inventory.csv", b"sku,quantity,status\nA-100,10,ok\nB-200,5,ok\n", "text/csv")),
                ("uploads", ("orders.csv", b"sku,quantity,status\nA-100,8,ok\nC-300,2,backorder\n", "text/csv")),
            ],
        )
        assert upload.status_code == 200
        files = client.get(f"/api/sessions/{session['id']}/files").json()
        assert {item["status"] for item in files} == {"ready"}

        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "Excel mode: reconcile these spreadsheets across files by sku and report mismatches."},
        )

        assert answer.status_code == 200
        payload = answer.json()
        assert payload["content"].startswith("Excel Mode reconciliation (local deterministic workflow)")
        assert "Compared 2 spreadsheet tables on shared key `sku`" in payload["content"]
        assert "`A-100` differs" in payload["content"]
        assert "inventory.csv / inventory row 2" in payload["content"]
        assert "orders.csv / orders row 2" in payload["content"]
        assert "`B-200` is missing from orders.csv / orders" in payload["content"]
        assert "`C-300` is missing from inventory.csv / inventory" in payload["content"]
        assert {citation["source_label"] for citation in payload["citations"]} == {"inventory.csv", "orders.csv"}
        html_artifacts = [artifact for artifact in payload["artifacts"] if artifact["title"] == "Spreadsheet Workflow Automator HTML app"]
        assert len(html_artifacts) == 1
        html_artifact = html_artifacts[0]
        assert html_artifact["kind"] == "file_draft"
        assert html_artifact["display_mode"] == "supporting"
        assert html_artifact["spec"]["filename"] == "spreadsheet-workflow-automator.html"
        assert html_artifact["spec"]["format"] == "html"
        assert html_artifact["spec"]["content"].startswith("<!doctype html>")
        assert "window.__WORKFLOW__" in html_artifact["spec"]["content"]
        assert "function duplicateKeyWarnings" in html_artifact["spec"]["content"]


def test_generic_summary_question_answers_from_ready_file(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "AI operations memo"}).json()
        body = (
            b"AI adoption and groupware operations memo.\n\n"
            b"This document summarizes the company's plan for AI pilot training, workflow discovery, "
            b"tool development, and groupware operations after a merger."
        )
        upload = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("memo.txt", body, "text/plain")},
        )
        assert upload.status_code == 200

        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "What is this about?"},
        )

        assert answer.status_code == 200
        payload = answer.json()
        assert payload["role"] == "assistant"
        assert payload["content"] != "I could not find that answer in the attached sources."
        assert payload["citations"]


def test_unrelated_question_can_return_grounded_refusal_without_citations(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        assert question == "What is the capital of France?"
        assert sources
        return ChatResult(answer="I could not find that answer in the attached sources.", cited_source_ids=[])

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Revenue memo"}).json()
        upload = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("revenue.txt", b"North America revenue rose because acquisition revenue expanded.", "text/plain")},
        )
        assert upload.status_code == 200

        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "What is the capital of France?"},
        )

        assert answer.status_code == 200
        payload = answer.json()
        assert payload["content"] == "I could not find that answer in the attached sources."
        assert payload["citations"] == []
        assert payload["grounding"]["status"] == "no_citations"
        assert payload["grounding"]["notice"] == "No citations attached to this answer."
        assert "no retrieved source snippets" in payload["grounding"]["detail"]


def test_reupload_same_file_reuses_cached_file(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        first = client.post("/api/sessions", json={"title": "First"}).json()
        second = client.post("/api/sessions", json={"title": "Second"}).json()
        body = b"Reusable file content about revenue and margin."

        one = client.post(
            f"/api/sessions/{first['id']}/files",
            files={"uploads": ("reuse.txt", body, "text/plain")},
        ).json()[0]
        two = client.post(
            f"/api/sessions/{second['id']}/files",
            files={"uploads": ("reuse.txt", body, "text/plain")},
        ).json()[0]

        assert one["id"] == two["id"]
        assert two["status"] == "ready"


def test_missing_session_resource_paths_return_404(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        assert client.delete("/api/sessions/ses_missing").status_code == 404
        assert client.get("/api/sessions/ses_missing/messages").status_code == 404
        assert client.get("/api/sessions/ses_missing/files").status_code == 404
        assert client.delete("/api/sessions/ses_missing/files/fil_missing").status_code == 404


def test_community_me_defaults_to_single_user_owner(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        payload = client.get("/api/me").json()

        assert payload["edition"] == "community"
        assert payload["role"] == "owner"
        assert payload["enterprise_enabled"] is False
        assert payload["capabilities"]["manage_provider_keys"] is True
        assert payload["capabilities"]["switch_test_mode"] is True


def test_local_mode_switcher_headers_toggle_edition_and_role(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        member_headers = {"X-FileChat-Test-Edition": "enterprise", "X-FileChat-Test-Role": "member"}
        admin_headers = {"X-FileChat-Test-Edition": "enterprise", "X-FileChat-Test-Role": "admin"}
        community_headers = {"X-FileChat-Test-Edition": "community", "X-FileChat-Test-Role": "member"}

        member = client.get("/api/me", headers=member_headers).json()
        assert member["edition"] == "enterprise"
        assert member["role"] == "member"
        assert member["auth_mode"] == "local_mode_switcher"
        assert member["capabilities"]["manage_provider_keys"] is False

        denied = client.patch("/api/admin/settings", json={"chat_model": "openai/denied"}, headers=member_headers)
        assert denied.status_code == 403

        admin_settings = client.get("/api/admin/settings", headers=admin_headers)
        assert admin_settings.status_code == 200
        assert admin_settings.json()["edition"] == "enterprise"
        assert admin_settings.json()["settings_scope"] == "organization"

        community = client.get("/api/me", headers=community_headers).json()
        assert community["edition"] == "community"
        assert community["role"] == "owner"
        assert community["capabilities"]["manage_provider_keys"] is True


def test_enterprise_ignores_auth_headers_until_trusted_adapter_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_EDITION", "enterprise")
    monkeypatch.setenv("FILECHAT_AUTH_TEST_MODE", "false")
    monkeypatch.setenv("FILECHAT_TRUSTED_AUTH_HEADERS", "false")

    with make_client(monkeypatch, tmp_path) as client:
        forged_owner = {
            "X-FileChat-User-Role": "owner",
            "X-FileChat-User-Id": "usr_forged_owner",
            "X-FileChat-Org-Id": "org_forged",
        }

        me = client.get("/api/me", headers=forged_owner).json()
        assert me["role"] == "member"
        assert me["organization_id"] == "org_single"
        assert me["auth_mode"] == "auth_required"
        assert me["capabilities"]["manage_provider_keys"] is False

        denied = client.patch("/api/admin/settings", json={"chat_model": "openai/not-allowed"}, headers=forged_owner)
        assert denied.status_code == 403


def test_enterprise_test_mode_gates_settings_and_audit_by_role(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_EDITION", "enterprise")
    monkeypatch.setenv("FILECHAT_AUTH_TEST_MODE", "true")

    with make_client(monkeypatch, tmp_path) as client:
        member = {"X-FileChat-Test-Role": "member"}
        admin = {"X-FileChat-Test-Role": "admin"}
        owner = {"X-FileChat-Test-Role": "owner"}

        me = client.get("/api/me", headers=admin).json()
        assert me["edition"] == "enterprise"
        assert me["auth_test_mode"] is True
        assert me["capabilities"]["use_admin_console"] is True

        denied = client.patch("/api/settings", json={"chat_model": "openai/not-allowed"}, headers=member)
        assert denied.status_code == 403

        allowed = client.patch("/api/admin/settings", json={"chat_model": "openai/enterprise"}, headers=admin)
        assert allowed.status_code == 200
        assert allowed.json()["settings_scope"] == "organization"
        assert allowed.json()["chat_model"] == "openai/enterprise"

        assert client.get("/api/admin/audit-events", headers=admin).status_code == 403
        audit = client.get("/api/admin/audit-events", headers=owner)
        assert audit.status_code == 200
        events = audit.json()
        assert events[0]["action"] == "settings.updated"
        assert events[0]["actor_role"] == "admin"
        assert events[0]["metadata"]["changed"] == ["chat_model"]


def test_enterprise_sessions_are_scoped_to_current_org(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_EDITION", "enterprise")
    monkeypatch.setenv("FILECHAT_AUTH_TEST_MODE", "true")

    with make_client(monkeypatch, tmp_path) as client:
        headers_one = {"X-FileChat-Test-Role": "member", "X-FileChat-Org-Id": "org_one"}
        headers_two = {"X-FileChat-Test-Role": "member", "X-FileChat-Org-Id": "org_two"}

        created = client.post("/api/sessions", json={"title": "Org one"}, headers=headers_one)
        assert created.status_code == 200

        assert len(client.get("/api/sessions", headers=headers_one).json()) == 1
        assert client.get("/api/sessions", headers=headers_two).json() == []
        assert client.get(f"/api/sessions/{created.json()['id']}", headers=headers_two).status_code == 404


def test_enterprise_file_identity_is_scoped_to_current_org(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_EDITION", "enterprise")
    monkeypatch.setenv("FILECHAT_AUTH_TEST_MODE", "true")

    with make_client(monkeypatch, tmp_path) as client:
        org_a = {"X-FileChat-Test-Role": "member", "X-FileChat-Org-Id": "org_alpha"}
        org_b = {"X-FileChat-Test-Role": "member", "X-FileChat-Org-Id": "org_beta"}
        body = b"Shared bytes about quarterly renewal risk and expansion signals."

        session_a = client.post("/api/sessions", json={"title": "Org alpha"}, headers=org_a).json()
        session_b = client.post("/api/sessions", json={"title": "Org beta"}, headers=org_b).json()

        file_a = client.post(
            f"/api/sessions/{session_a['id']}/files",
            files={"uploads": ("alpha.txt", body, "text/plain")},
            headers=org_a,
        ).json()[0]
        file_b = client.post(
            f"/api/sessions/{session_b['id']}/files",
            files={"uploads": ("beta.txt", body, "text/plain")},
            headers=org_b,
        ).json()[0]

        assert file_a["hash"] == file_b["hash"]
        assert file_a["id"] != file_b["id"]
        assert [item["id"] for item in client.get(f"/api/sessions/{session_a['id']}/files", headers=org_a).json()] == [
            file_a["id"]
        ]
        assert [item["id"] for item in client.get(f"/api/sessions/{session_b['id']}/files", headers=org_b).json()] == [
            file_b["id"]
        ]

        assert client.get(f"/api/files/{file_a['id']}/status", headers=org_b).status_code == 404
        assert client.post(f"/api/sessions/{session_b['id']}/files/{file_a['id']}/retry", headers=org_b).status_code == 404

        answer = client.post(
            f"/api/sessions/{session_b['id']}/messages",
            json={"content": "What is this about?"},
            headers=org_b,
        )

        assert answer.status_code == 200
        citation_file_ids = {citation["file_id"] for citation in answer.json()["citations"]}
        assert citation_file_ids == {file_b["id"]}


def test_enterprise_retrieval_ignores_cross_org_attachment_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_EDITION", "enterprise")
    monkeypatch.setenv("FILECHAT_AUTH_TEST_MODE", "true")

    with make_client(monkeypatch, tmp_path) as client:
        org_a = {"X-FileChat-Test-Role": "member", "X-FileChat-Org-Id": "org_alpha"}
        org_b = {"X-FileChat-Test-Role": "member", "X-FileChat-Org-Id": "org_beta"}
        session_a = client.post("/api/sessions", json={"title": "Org alpha"}, headers=org_a).json()
        session_b = client.post("/api/sessions", json={"title": "Org beta"}, headers=org_b).json()

        file_a = client.post(
            f"/api/sessions/{session_a['id']}/files",
            files={"uploads": ("alpha.txt", b"Org alpha confidential renewal plan.", "text/plain")},
            headers=org_a,
        ).json()[0]
        with connect() as conn:
            conn.execute(
                "INSERT INTO session_files (session_id, file_id, attached_at) VALUES (?, ?, ?)",
                (session_b["id"], file_a["id"], now()),
            )

        assert client.get(f"/api/sessions/{session_b['id']}/files", headers=org_b).json() == []
        answer = client.post(
            f"/api/sessions/{session_b['id']}/messages",
            json={"content": "What is this about?"},
            headers=org_b,
        )

        assert answer.status_code == 200
        assert answer.json()["citations"] == []


def test_detach_missing_file_attachment_returns_404(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Detach"}).json()

        missing = client.delete(f"/api/sessions/{session['id']}/files/fil_missing")

        assert missing.status_code == 404


def test_retry_failed_file_requeues_and_reprocesses(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Retry"}).json()
        uploaded = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("retry.txt", b"Retryable source text about revenue.", "text/plain")},
        ).json()[0]

        with connect() as conn:
            conn.execute(
                "UPDATE files SET status = ?, progress = ?, error = ? WHERE id = ?",
                ("failed", 1.0, "OpenRouter authentication failed.", uploaded["id"]),
            )

        retry = client.post(f"/api/sessions/{session['id']}/files/{uploaded['id']}/retry")

        assert retry.status_code == 200
        assert retry.json()["status"] == "queued"
        files = client.get(f"/api/sessions/{session['id']}/files").json()
        assert files[0]["status"] == "ready"
        assert files[0]["error"] is None


def test_saved_openrouter_key_overrides_stale_env_key(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path):
        monkeypatch.setenv("OPENROUTER_API_KEY", "stale-env-key")
        monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
        get_settings.cache_clear()
        set_setting("openrouter_api_key", "fresh-local-key")

        key, source = get_openrouter_key()

        assert key == "fresh-local-key"
        assert source == "local"


def test_openrouter_key_save_falls_back_to_db_when_keyring_readback_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setattr("backend.app.settings_store._keyring_set", lambda value: True)
    monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
    get_settings.cache_clear()

    with TestClient(app) as client:
        saved = client.patch("/api/settings", json={"openrouter_api_key": "sk-or-...cret"})

        assert saved.status_code == 200
        payload = saved.json()
        assert payload["openrouter_key_configured"] is True
        assert payload["openrouter_key_source"] == "local"
        key, source = get_openrouter_key()
        assert key == "sk-or-...cret"
        assert source == "local"


def test_openrouter_key_save_uses_new_local_key_even_when_env_key_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "stale-env-key")
    monkeypatch.setattr("backend.app.settings_store._keyring_set", lambda value: True)
    monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
    get_settings.cache_clear()

    with TestClient(app) as client:
        saved = client.patch("/api/settings", json={"openrouter_api_key": "fresh-local-key"})

        assert saved.status_code == 200
        payload = saved.json()
        assert payload["openrouter_key_configured"] is True
        assert payload["openrouter_key_source"] == "local"
        assert payload["openrouter_provider_status"] == "unverified"
        key, source = get_openrouter_key()
        assert key == "fresh-local-key"
        assert source == "local"


def test_clear_local_openrouter_key_resets_provider_state(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
    monkeypatch.setattr("backend.app.settings_store._keyring_delete", lambda: True)
    get_settings.cache_clear()

    with TestClient(app) as client:
        set_setting("openrouter_api_key", "sk-or-local-secret")
        set_setting("openrouter_provider_status", "verified")
        set_setting("openrouter_provider_message", "OpenRouter key verified.")

        cleared = client.delete("/api/admin/settings/openrouter-key")

        assert cleared.status_code == 200
        payload = cleared.json()
        assert payload["openrouter_key_configured"] is False
        assert payload["openrouter_key_source"] == "missing"
        assert payload["openrouter_provider_status"] == "missing"
        key, source = get_openrouter_key()
        assert key is None
        assert source == "missing"


def test_clear_openrouter_key_refuses_env_key(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
    get_settings.cache_clear()

    with TestClient(app) as client:
        cleared = client.delete("/api/admin/settings/openrouter-key")

        assert cleared.status_code == 409
        assert "Environment" in cleared.json()["detail"]


def test_audit_events_are_redacted_and_immutable(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        principal = Principal(
            user_id="usr_owner",
            display_name="Owner",
            email="owner@example.com",
            role="owner",
            organization_id="org_single",
            edition="community",
            auth_test_mode=False,
            auth_mode="single_user",
        )
        event_id = record_audit_event(
            principal,
            action="security.test",
            target_type="file",
            target_id="fil_test",
            metadata={
                "api_key": "sk-or-secret",
                "path": "/tmp/private/report.pdf",
                "nested": {"content": "raw file text", "safe": "Bearer abc123"},
            },
        )

        audit = client.get("/api/admin/audit-events")

        assert audit.status_code == 200
        event = next(item for item in audit.json() if item["id"] == event_id)
        assert event["metadata"]["api_key"] == "[redacted]"
        assert event["metadata"]["path"] == "[redacted]"
        assert event["metadata"]["nested"]["content"] == "[redacted]"
        assert event["metadata"]["nested"]["safe"] == "[redacted]"
        with pytest.raises(Exception, match="immutable"):
            with connect() as conn:
                conn.execute("UPDATE audit_events SET action = ? WHERE id = ?", ("mutated", event_id))


def test_meta_issue_create_list_and_status_update_are_sanitized(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        created = client.post(
            "/api/meta-issues",
            json={
                "source": "complaint",
                "severity": "error",
                "title": "Runtime failed with sk-or-secret",
                "body": "User saw Bearer abc123 while opening /tmp/private/report.pdf",
                "metadata": {"path": "/tmp/private/report.pdf", "safe": "request failed"},
            },
        )

        assert created.status_code == 200
        issue = created.json()
        assert issue["title"] == "Runtime failed with [redacted]"
        assert "[redacted]" in issue["body"]
        assert issue["metadata"]["path"] == "[redacted]"
        assert issue["metadata"]["safe"] == "request failed"
        assert issue["status"] == "open"

        listed = client.get("/api/admin/meta-issues")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == issue["id"]

        updated = client.patch(f"/api/admin/meta-issues/{issue['id']}", json={"status": "triaged"})
        assert updated.status_code == 200
        assert updated.json()["status"] == "triaged"


def test_enterprise_member_cannot_read_meta_issues(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_EDITION", "enterprise")
    monkeypatch.setenv("FILECHAT_AUTH_TEST_MODE", "true")

    with make_client(monkeypatch, tmp_path) as client:
        denied = client.get("/api/admin/meta-issues", headers={"X-FileChat-Test-Role": "member"})

        assert denied.status_code == 403


def test_meta_issue_can_create_github_issue_when_configured(monkeypatch, tmp_path):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"html_url": "https://github.com/nrtvai/filechat/issues/99"}

    async def fake_post(self, url, headers=None, json=None):
        assert url == "https://api.github.com/repos/nrtvai/filechat/issues"
        assert headers["Authorization"] == "Bearer gh-test"
        assert "sk-or-secret" not in json["title"]
        return FakeResponse()

    monkeypatch.setenv("FILECHAT_META_ISSUES_GITHUB_ENABLED", "true")
    monkeypatch.setenv("FILECHAT_META_ISSUES_GITHUB_REPO", "nrtvai/filechat")
    monkeypatch.setenv("FILECHAT_META_ISSUES_GITHUB_TOKEN", "gh-test")
    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    with make_client(monkeypatch, tmp_path) as client:
        created = client.post(
            "/api/meta-issues",
            json={"source": "runtime", "severity": "error", "title": "Secret sk-or-secret", "body": "failed"},
        )

        assert created.status_code == 200
        assert created.json()["external_url"] == "https://github.com/nrtvai/filechat/issues/99"


def test_wiki_graph_nodes_and_edges_are_org_scoped_and_sanitized(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_EDITION", "enterprise")
    monkeypatch.setenv("FILECHAT_AUTH_TEST_MODE", "true")

    with make_client(monkeypatch, tmp_path) as client:
        org_a = {"X-FileChat-Test-Role": "member", "X-FileChat-Org-Id": "org_alpha"}
        org_b = {"X-FileChat-Test-Role": "member", "X-FileChat-Org-Id": "org_beta"}

        source = client.post(
            "/api/wiki/nodes",
            headers=org_a,
            json={
                "scope": "organization",
                "type": "topic",
                "title": "Customer onboarding",
                "summary": "Sensitive token sk-or-secret should be hidden",
                "properties": {"api_key": "sk-or-secret", "status": "draft"},
                "source_refs": [{"path": "/tmp/customer-list.csv", "id": "fil_safe"}],
            },
        )
        target = client.post(
            "/api/wiki/nodes",
            headers=org_a,
            json={"scope": "user", "type": "preference", "title": "My tone", "summary": "Concise"},
        )

        assert source.status_code == 200
        assert target.status_code == 200
        source_node = source.json()
        target_node = target.json()
        assert source_node["summary"] == "Sensitive token [redacted] should be hidden"
        assert source_node["properties"]["api_key"] == "[redacted]"
        assert source_node["source_refs"][0]["path"] == "[redacted]"
        assert target_node["scope"] == "user"
        assert target_node["owner_user_id"] == "usr_test_member"

        edge = client.post(
            "/api/wiki/edges",
            headers=org_a,
            json={
                "source_node_id": source_node["id"],
                "target_node_id": target_node["id"],
                "relation_type": "informs",
                "weight": 0.8,
                "confidence": 0.7,
                "properties": {"note": "manual"},
            },
        )
        assert edge.status_code == 200
        assert edge.json()["relation_type"] == "informs"
        assert client.get("/api/wiki/edges", headers=org_a).json()[0]["id"] == edge.json()["id"]

        assert client.get(f"/api/wiki/nodes/{source_node['id']}", headers=org_b).status_code == 404
        assert client.get("/api/wiki/nodes", headers=org_b).json() == []
        assert client.post(
            "/api/wiki/edges",
            headers=org_b,
            json={
                "source_node_id": source_node["id"],
                "target_node_id": target_node["id"],
                "relation_type": "blocked",
            },
        ).status_code == 404

        patched = client.patch(
            f"/api/wiki/nodes/{source_node['id']}",
            headers=org_a,
            json={"summary": "Updated"},
        )
        assert patched.status_code == 200
        assert patched.json()["summary"] == "Updated"

        assert client.delete(f"/api/wiki/edges/{edge.json()['id']}", headers=org_a).json() == {"ok": True}
        assert client.delete(f"/api/wiki/nodes/{target_node['id']}", headers=org_a).json() == {"ok": True}


def test_slack_webhook_verifies_signature_and_queues_inline_attachment(monkeypatch, tmp_path):
    secret = "slack-signing-secret"
    monkeypatch.setenv("FILECHAT_SLACK_SIGNING_SECRET", secret)

    with make_client(monkeypatch, tmp_path) as client:
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "files": [
                    {
                        "name": "slack-notes.txt",
                        "mimetype": "text/plain",
                        "content": "Slack attachment source text.",
                    }
                ],
            },
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        response = client.post("/api/integrations/slack/events", content=body, headers=slack_headers(secret, body))

        assert response.status_code == 200
        result = response.json()
        assert result["service"] == "slack"
        assert result["accepted"] == 1
        assert result["files"][0]["status"] in {"queued", "ready"}


def test_slack_webhook_rejects_bad_signature_and_logs_meta_issue(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_SLACK_SIGNING_SECRET", "slack-signing-secret")

    with make_client(monkeypatch, tmp_path) as client:
        body = b'{"type":"event_callback"}'
        response = client.post(
            "/api/integrations/slack/events",
            content=body,
            headers={"X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=bad"},
        )

        assert response.status_code == 401
        issues = client.get("/api/admin/meta-issues").json()
        assert issues[0]["source"] == "bot"
        assert "Slack webhook rejected" == issues[0]["title"]


def test_telegram_webhook_verifies_secret_and_queues_inline_attachment(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_TELEGRAM_WEBHOOK_SECRET", "telegram-secret")

    with make_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/integrations/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "message": {
                    "document": {
                        "file_name": "telegram-note.txt",
                        "mime_type": "text/plain",
                        "content": "Telegram attachment source text.",
                    }
                }
            },
        )

        assert response.status_code == 200
        assert response.json()["service"] == "telegram"
        assert response.json()["accepted"] == 1


def test_telegram_webhook_rejects_missing_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_TELEGRAM_WEBHOOK_SECRET", "telegram-secret")

    with make_client(monkeypatch, tmp_path) as client:
        response = client.post("/api/integrations/telegram/webhook", json={"message": {}})

        assert response.status_code == 401
        assert client.get("/api/admin/meta-issues").json()[0]["source"] == "bot"


def test_enterprise_readiness_surfaces_share_redaction_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_SLACK_SIGNING_SECRET", "slack-signing-secret")

    with make_client(monkeypatch, tmp_path) as client:
        meta = client.post(
            "/api/meta-issues",
            json={
                "source": "runtime",
                "severity": "error",
                "title": "Leaked sk-or-secret",
                "metadata": {"filename": "private.pdf", "token": "Bearer abc123"},
            },
        ).json()
        node = client.post(
            "/api/wiki/nodes",
            json={
                "scope": "organization",
                "type": "note",
                "title": "Secret node",
                "properties": {"path": "/tmp/private.pdf", "api_key": "sk-or-secret"},
            },
        ).json()
        client.post(
            "/api/integrations/slack/events",
            content=b'{"type":"event_callback"}',
            headers={"X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=bad"},
        )

        bot_issue = next(issue for issue in client.get("/api/admin/meta-issues").json() if issue["source"] == "bot")

        assert meta["title"] == "Leaked [redacted]"
        assert meta["metadata"]["filename"] == "[redacted]"
        assert meta["metadata"]["token"] == "[redacted]"
        assert node["properties"]["path"] == "[redacted]"
        assert node["properties"]["api_key"] == "[redacted]"
        assert bot_issue["source"] == "bot"
        assert bot_issue["metadata"]["reason"]


def test_missing_unverified_provider_blocks_model_run(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
    get_settings.cache_clear()

    with TestClient(app) as client:
        session = client.post("/api/sessions", json={"title": "No provider"}).json()
        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Summarize"})

        assert started.status_code == 200
        payload = started.json()
        assert payload["status"] == "needs_setup"
        assert "OpenRouter" in payload["error"]
        assert [(action["kind"], action["status"]) for action in payload["actions"]] == [("verify_provider", "failed")]


def test_verify_missing_provider_returns_settings_not_internal_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
    get_settings.cache_clear()

    with TestClient(app) as client:
        verified = client.post("/api/settings/openrouter/verify")

        assert verified.status_code == 200
        payload = verified.json()
        assert payload["openrouter_provider_status"] == "missing"
        assert "API key" in payload["openrouter_provider_message"]


def test_persisted_action_json_redacts_secrets(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Redaction"}).json()
        run = create_agent_run(session["id"], "Summarize")

        set_action(
            run.id,
            "reason",
            "completed",
            output_summary="Prepared decision summary",
            input_json={"Authorization": "Bearer secret-token", "nested": {"api_key": "sk-or-secret"}},
            output_json={"safe": "ok", "raw": "secret raw payload"},
        )

        saved = get_agent_run(run.id)
        assert saved is not None
        action = saved.actions[0]
        assert action.input_json["Authorization"] == "[redacted]"
        assert action.input_json["nested"]["api_key"] == "[redacted]"
        assert action.output_json["raw"] == "[redacted]"


def test_no_source_run_records_only_actual_actions(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "No source"}).json()

        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Summarize this"})

        assert started.status_code == 200
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert [action["kind"] for action in run["actions"]] == [
            "verify_provider",
            "classify_request",
            "plan_task",
            "load_sources",
            "persist_response",
        ]


def test_openrouter_verify_endpoint_marks_provider_verified(monkeypatch, tmp_path):
    async def fake_verify_provider(self, *, chat_model, embedding_model):
        return {"status": "verified", "message": "OpenRouter key verified.", "models_checked": [chat_model, embedding_model]}

    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
    monkeypatch.setattr(OpenRouterClient, "verify_provider", fake_verify_provider)
    get_settings.cache_clear()

    with TestClient(app) as client:
        verified = client.post("/api/settings/openrouter/verify")

        assert verified.status_code == 200
        assert verified.json()["openrouter_provider_status"] == "verified"
        assert verified.json()["openrouter_key_source"] == "env"


def test_context_profile_defaults_and_patch(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        profile = client.get("/api/context/profile")
        assert profile.status_code == 200
        assert profile.json()["artifact_policy"] == "chart+draft"
        assert profile.json()["citation_display"] == "minimized"

        updated = client.patch("/api/context/profile", json={"citation_display": "full"})

        assert updated.status_code == 200
        assert updated.json()["citation_display"] == "full"


def test_models_endpoint_normalizes_openrouter_models(monkeypatch, tmp_path):
    async def fake_models(self, kind):
        assert kind == "chat"
        return [
            {
                "id": "openai/gpt-test",
                "name": "GPT Test",
                "context_length": 128000,
                "pricing": {"prompt": 0.0000001, "completion": 0.0000002, "request": 0.0, "image": 0.0},
                "created": 123,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["response_format"],
            }
        ]

    monkeypatch.setattr("backend.app.main.OpenRouterClient.models", fake_models)

    with make_client(monkeypatch, tmp_path) as client:
        payload = client.get("/api/models?kind=chat").json()

        assert payload[0]["id"] == "openai/gpt-test"
        assert payload[0]["pricing"]["prompt"] == 0.0000001
        assert payload[0]["supported_parameters"] == ["response_format"]


def test_provider_registry_keeps_openrouter_as_active_provider():
    provider = provider_registry().active()

    assert provider.id == DEFAULT_PROVIDER_ID
    assert provider.display_name == "OpenRouter"


def test_chat_usage_is_stored_on_user_and_assistant_messages(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="Revenue rose because acquisition revenue expanded.",
            cited_source_ids=[1],
            model=model,
            usage=UsageInfo(
                prompt_tokens=100,
                completion_tokens=25,
                total_tokens=125,
                prompt_cost=0.001,
                completion_cost=0.002,
                total_cost=0.003,
            ),
        )

    async def fake_embedding_result(self, inputs, model):
        return EmbeddingResult(
            vectors=[[1.0, 0.0] for _ in inputs],
            model=model,
            usage=UsageInfo(prompt_tokens=7, total_tokens=7, prompt_cost=0.0001, total_cost=0.0001),
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)
    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.embedding_result", fake_embedding_result)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Usage"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("usage.txt", b"North America revenue rose because acquisition revenue expanded.", "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Why did revenue rise?"})

        assert answer.status_code == 200
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        user = messages[0]
        assistant = messages[1]
        assert user["prompt_tokens"] == 107
        assert user["total_cost"] == 0.0011
        assert assistant["completion_tokens"] == 25
        assert assistant["total_cost"] == 0.002

        summary = client.get(f"/api/sessions/{session['id']}/usage").json()
        assert summary["chat_prompt_cost"] == 0.001
        assert summary["chat_completion_cost"] == 0.002
        assert summary["embedding_cost"] >= 0.0001
        assert summary["total_cost"] >= 0.0031


def test_follow_up_flowchart_request_uses_history_and_persists_mermaid_artifact(monkeypatch, tmp_path):
    embedding_inputs = []

    async def fake_embedding_result(self, inputs, model):
        embedding_inputs.extend(inputs)
        return EmbeddingResult(vectors=[[1.0, 0.0] for _ in inputs], model=model, usage=UsageInfo())

    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        if question == "요약해 주세요":
            return ChatResult(
                answer="전자결재는 하이웍스 활용이 기본이며, 원하시면 흐름도로 정리할 수 있습니다.",
                cited_source_ids=[1],
                model=model,
            )
        assert question == "해보세요"
        assert history
        assert any("흐름도" in item["content"] for item in history)
        return ChatResult(
            answer="요청하신 내용을 흐름도로 정리했습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "mermaid",
                    "title": "AI 도입 흐름",
                    "caption": "파일럿 중심 도입 절차",
                    "source_ids": [1],
                    "diagram": "flowchart TD\n  A[파일럿 검토] --> B[교육]\n  B --> C[확대 판단]",
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.embedding_result", fake_embedding_result)
    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)
    monkeypatch.setattr("backend.app.ingest.OpenRouterClient.embedding_result", fake_embedding_result)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "AI plan"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("ai-plan.txt", "AI 도입은 파일럿 검토, 교육, 확대 판단 순서로 진행한다.".encode("utf-8"), "text/plain")},
        )

        first = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "요약해 주세요"})
        second = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "해보세요"})

        assert first.status_code == 200
        assert second.status_code == 200
        payload = second.json()
        assert payload["artifacts"][0]["kind"] == "mermaid"
        assert payload["artifacts"][0]["spec"]["diagram"].startswith("flowchart TD")
        assert payload["artifacts"][0]["source_chunk_ids"]
        assert any("흐름도" in text and "해보세요" in text for text in embedding_inputs)


def test_json_render_artifact_is_validated_and_returned(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="표로 정리했습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "table",
                    "title": "도입 항목",
                    "caption": "문서 근거 기반 표",
                    "source_ids": [1],
                    "jsonRenderSpec": {
                        "root": "card",
                        "elements": {
                            "card": {"type": "ArtifactCard", "props": {"title": "도입 항목"}, "children": ["table"]},
                            "table": {
                                "type": "DataTable",
                                "props": {"columns": ["항목", "내용"], "rows": [["교육", "파일럿 그룹"]]},
                                "children": [],
                            },
                        },
                    },
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Artifact"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("artifact.txt", "교육 대상은 파일럿 그룹이다.".encode("utf-8"), "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "표로 정리해 주세요"})

        assert answer.status_code == 200
        artifact = answer.json()["artifacts"][0]
        assert artifact["kind"] == "table"
        assert artifact["spec"]["root"] == "card"
        assert artifact["spec"]["elements"]["table"]["type"] == "DataTable"


def test_nested_json_render_artifact_is_normalized_and_returned(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="인사이트 패널을 만들었습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "summary_panel",
                    "title": "Survey insights",
                    "caption": "문서 근거 기반 요약",
                    "source_ids": [1],
                    "jsonRenderSpec": {
                        "root": {
                            "type": "ArtifactCard",
                            "props": {"title": "Survey insights"},
                            "children": [
                                {"type": "TextBlock", "props": {"text": "반복 업무가 주요 병목입니다."}, "children": []}
                            ],
                        }
                    },
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Nested Artifact"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("artifact.txt", "반복 업무가 병목이다.".encode("utf-8"), "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "show me insights"})

        assert answer.status_code == 200
        artifact = answer.json()["artifacts"][0]
        assert artifact["kind"] == "summary_panel"
        assert artifact["spec"]["root"] == "root"
        assert artifact["spec"]["elements"]["root"]["type"] == "ArtifactCard"


def test_docx_artifact_discovery_returns_json_render_options(monkeypatch, tmp_path):
    async def fail_chat(self, *, model, question, sources, unavailable, history=None, prompt_context=None):
        raise AssertionError("artifact discovery should not call model writing")

    monkeypatch.setattr(OpenRouterClient, "chat", fail_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "AI memo"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={
                "uploads": (
                    "memo.txt",
                    "AI adoption memo. 4월 proposal review. 5월 training. 6월 consulting and tool build.".encode("utf-8"),
                    "text/plain",
                )
            },
        )
        with connect() as conn:
            conn.execute("UPDATE files SET name = ?, type = ? WHERE organization_id = ?", ("ai-roadmap.docx", "DOCX", "org_single"))

        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "what charts and docs can you make with this?"},
        )

        assert answer.status_code == 200
        payload = answer.json()
        assert "could not render" not in payload["content"].lower()
    assert payload["artifacts"][0]["kind"] == "decision_cards"
    spec = payload["artifacts"][0]["spec"]
    assert spec["root"] == "card"
    assert "visible" not in spec["elements"]["card"]
    assert not any(element["type"] == "ActionButton" for element in spec["elements"].values())
    assert spec["decision_options"]
    assert all(option["produce_payload"] for option in spec["decision_options"])


def test_artifact_discovery_fallback_options_do_not_use_stale_ai_or_groupware_labels(monkeypatch, tmp_path):
    async def fail_chat(self, *, model, question, sources, unavailable, history=None, prompt_context=None):
        raise AssertionError("artifact discovery should not call model writing")

    monkeypatch.setattr(OpenRouterClient, "chat", fail_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Facilities memo"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={
                "uploads": (
                    "facilities.txt",
                    b"Facilities maintenance memo. March roof inspection. April safety training. May budget review.",
                    "text/plain",
                )
            },
        )

        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "what charts and docs can you make with this?"},
        )

        assert answer.status_code == 200
        spec = answer.json()["artifacts"][0]["spec"]
        serialized = json.dumps(spec)
        assert "AI adoption" not in serialized
        assert "Groupware" not in serialized
        assert "Facilities" in serialized or "Source" in serialized
        assert spec["decision_options"]
        assert all(option["produce_payload"] for option in spec["decision_options"])


def test_timeline_roadmap_renders_as_json_summary_artifact(monkeypatch, tmp_path):
    async def fail_chat(self, *, model, question, sources, unavailable, history=None, prompt_context=None):
        raise AssertionError("timeline roadmap should use the local JSON renderer path")

    monkeypatch.setattr(OpenRouterClient, "chat", fail_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Roadmap"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={
                "uploads": (
                    "roadmap.txt",
                    "AI adoption roadmap: 4월 제안서 검토. 5월 기초 교육. 6월 컨설팅 및 툴 제작. 7월 이후 AS.".encode("utf-8"),
                    "text/plain",
                )
            },
        )

        answer = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "Create the AI adoption roadmap chart"},
        )

        assert answer.status_code == 200
        payload = answer.json()
        artifact = payload["artifacts"][0]
        assert artifact["kind"] == "summary_panel"
        assert artifact["spec"]["elements"]["timeline"]["type"] == "Timeline"
        assert "visible" not in artifact["spec"]["elements"]["timeline"]
        assert artifact["spec"]["elements"]["timeline"]["props"]["items"][0]["date"] == "4월"
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert run["task_contract"]["required_outputs"] == ["summary_panel"]


def test_sales_order_timeline_includes_concise_summary_panel_and_citations(monkeypatch, tmp_path):
    async def fake_review_phase(
        self,
        *,
        model,
        phase,
        phase_goal,
        task_contract,
        evidence_packet,
        source_refs,
        artifact_specs,
        answer_draft,
        prior_checker_reports=None,
    ):
        serialized = json.dumps(artifact_specs, ensure_ascii=False)
        summary_panels = [artifact for artifact in artifact_specs if artifact.get("kind") == "summary_panel"]
        raw_csv_leak = "order_id,SKU" in serialized or "SO-1001,HB-100" in serialized
        answer_mentions_both = "timeline panel" in answer_draft.lower() and "summary panel" in answer_draft.lower()
        if raw_csv_leak or len(summary_panels) < 2 or not answer_mentions_both or not source_refs:
            return {
                "phase": phase,
                "passed": False,
                "severity": "high",
                "findings": [
                    "Timeline output must avoid raw CSV dumps, include both panels, and remain source-grounded."
                ],
                "required_fixes": [
                    "Create a concise timeline panel plus a concise sales orders summary panel with citations."
                ],
                "suggested_followups": [],
                "confidence": "high",
            }
        return {
            "phase": phase,
            "passed": True,
            "severity": "none",
            "findings": [],
            "required_fixes": [],
            "suggested_followups": [],
            "confidence": "high",
        }

    monkeypatch.setattr(OpenRouterClient, "review_phase", fake_review_phase)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Sales order timeline"}).json()
        body = Path("test_documents/correlated_business/sales_orders.csv").read_bytes()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("sales_orders.csv", body, "text/csv")},
        )

        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Create a sales orders timeline panel"})

        assert started.status_code == 200
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        latest = messages[-1]
        assert latest["citations"]
        assert "timeline panel" in latest["content"].lower()
        assert "summary panel" in latest["content"].lower()
        panels = [artifact for artifact in latest["artifacts"] if artifact["kind"] == "summary_panel"]
        assert len(panels) == 2
        for panel in panels:
            assert panel["source_chunk_ids"]
        serialized = json.dumps([panel["spec"] for panel in panels], ensure_ascii=False)
        assert "order_id,SKU" not in serialized
        assert "SO-1001,HB-100" not in serialized


def test_timeline_json_render_spec_validates_but_timeline_chart_type_does_not():
    sources = [{"source_id": 1, "chunk_id": "chk_1"}]
    valid = validate_artifacts_with_report(
        [
            {
                "kind": "summary_panel",
                "title": "Roadmap",
                "source_ids": [1],
                "jsonRenderSpec": {
                    "root": "card",
                    "elements": {
                        "card": {"type": "ArtifactCard", "props": {"title": "Roadmap"}, "children": ["timeline"]},
                        "timeline": {
                            "type": "Timeline",
                            "props": {"items": [{"date": "4월", "label": "Kickoff", "description": "Start", "sourceChunkId": "chk_1"}]},
                            "children": [],
                        },
                    },
                },
            }
        ],
        sources,
    )
    invalid = validate_artifacts_with_report(
        [
            {
                "kind": "chart",
                "title": "Roadmap",
                "source_ids": [1],
                "chart_type": "timeline",
                "values": [{"label": "4월", "value": 1, "source_id": 1}],
            }
        ],
        sources,
    )

    assert valid.artifacts[0].spec["elements"]["timeline"]["type"] == "Timeline"
    assert invalid.artifacts == []
    assert "timeline chart" in invalid.warnings[0]


def test_unsupported_timeline_chart_does_not_complete_as_prose_only(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None, prompt_context=None):
        return ChatResult(
            answer="Here is the roadmap chart.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "chart",
                    "title": "Roadmap",
                    "source_ids": [1],
                    "chart_type": "timeline",
                    "values": [{"label": "Kickoff", "value": 1, "source_id": 1}],
                }
            ],
            model=model,
        )

    monkeypatch.setattr(OpenRouterClient, "chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Bad timeline"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("source.txt", b"Source text for a process chart.", "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Make a chart"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]

        assert answer.status_code == 200
        assert run["status"] == "needs_revision"
        assert "timeline chart" in run["error"]
        assert answer.json()["artifacts"] == []
        assert "could not render" not in answer.json()["content"].lower()


def test_invalid_artifact_specs_are_not_persisted(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="정리했습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "table",
                    "title": "Broken",
                    "source_ids": [1],
                    "jsonRenderSpec": {
                        "root": "bad",
                        "elements": {
                            "bad": {"type": "UnsafeHtml", "props": {"html": "<script />"}, "children": []},
                        },
                    },
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Invalid Artifact"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("artifact.txt", b"Grounded source text about workflow.", "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Make UI"})

        assert answer.status_code == 200
        assert answer.json()["artifacts"] == []


def test_file_indexing_embedding_usage_is_attributed_to_session(monkeypatch, tmp_path):
    async def fake_embedding_result(self, inputs, model):
        return EmbeddingResult(
            vectors=[[1.0, 0.0] for _ in inputs],
            model=model,
            usage=UsageInfo(prompt_tokens=11, total_tokens=11, prompt_cost=0.00022, total_cost=0.00022),
        )

    monkeypatch.setattr("backend.app.ingest.OpenRouterClient.embedding_result", fake_embedding_result)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Index cost"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("index.txt", b"Indexable source text about revenue and margin.", "text/plain")},
        )

        files = client.get(f"/api/sessions/{session['id']}/files").json()
        assert files[0]["indexing_prompt_tokens"] == 11
        assert files[0]["indexing_total_cost"] == 0.00022

        summary = client.get(f"/api/sessions/{session['id']}/usage").json()
        assert summary["embedding_tokens"] == 11
        assert summary["embedding_cost"] == 0.00022


@pytest.mark.asyncio
async def test_embedding_response_missing_data_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setattr("backend.app.openrouter.get_openrouter_key", lambda: ("key", "local"))
    get_settings.cache_clear()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"error": {"message": "provider omitted data"}}

    async def fake_post(self, *args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    with pytest.raises(OpenRouterResponseError, match="Embedding model returned no vectors"):
        await OpenRouterClient().embedding_result(["survey"], "openai/text-embedding-3-small")


@pytest.mark.asyncio
async def test_chat_response_missing_choices_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setattr("backend.app.openrouter.get_openrouter_key", lambda: ("key", "local"))
    get_settings.cache_clear()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": []}

    async def fake_post(self, *args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    with pytest.raises(OpenRouterResponseError, match="did not return a completion choice"):
        await OpenRouterClient().chat(
            model="openai/gpt-4o-mini",
            question="Make a chart",
            sources=[{"source_id": 1, "file_name": "survey.csv", "location": "chunk 1", "content": "Yes,10", "excerpt": "Yes,10"}],
            unavailable=[],
        )


def test_agent_run_persists_adaptive_actions(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Run"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("run.txt", b"Survey result\nYes,10\nNo,4", "text/plain")},
        )

        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Make a chart about the survey result"})

        assert started.status_code == 200
        runs = client.get(f"/api/sessions/{session['id']}/runs").json()
        assert runs[0]["status"] == "completed"
        assert "steps" not in runs[0]
        assert [action["kind"] for action in runs[0]["actions"]] == [
            "verify_provider",
            "classify_request",
            "plan_task",
            "load_sources",
            "rank_sources",
            "profile_table",
            "build_evidence",
            "write",
            "validate",
            "persist_response",
        ]
        assert runs[0]["assistant_message_id"]
        assert runs[0]["execution_plan"]["requested_outputs"] == ["chart"]
        assert runs[0]["execution_plan"]["reasoning_required"] is True
        assert runs[0]["model_assignments"]["analysis"]["reasoning_effort"] == "medium"
        assert any(call["tool"] == "source_profiler" for call in runs[0]["tool_calls"])


def test_business_csv_request_profiles_table_not_survey(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Warehouse"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={
                "uploads": (
                    "warehouse_stock_units.csv",
                    b"SKU,warehouse,category,units_on_hand,reorder_point,unit_cost\nHB-100,North,Hardware,18,30,12.50\nHB-200,South,Hardware,90,40,8.25\n",
                    "text/csv",
                )
            },
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Draft a Notion-ready operations report with a replenishment table."})

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert any(call["tool"] == "source_profiler" for call in run["tool_calls"])
        assert any(action["kind"] == "profile_table" for action in run["actions"])


def test_ambiguous_best_graph_monthly_revenue_auto_creates_line_recommendation(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Revenue"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={
                "uploads": (
                    "monthly_revenue.csv",
                    b"Month,Revenue\n2026-01,100\n2026-02,125\n2026-03,160\n2026-04,155\n",
                    "text/csv",
                )
            },
        )

        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "best graph for this file"})

        assert started.status_code == 200
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert run["current_question"] is None
        profile = next(action for action in run["actions"] if action["kind"] == "profile_table")
        assert profile["output_json"]["artifact_recommendations"][0]["chart_type"] == "line"
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        recommendations = next(item for item in workspace if item["path"] == "/analysis/artifact-recommendations.json")
        assert recommendations["content"]["recommendations"][0]["source_columns"] == ["Month", "Revenue"]
        insight_brief = next(item for item in workspace if item["path"] == "/analysis/insight-brief.json")
        assert insight_brief["content"]["insights"][0]["framework"] == "trend"
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        chart = messages[-1]["artifacts"][0]
        assert chart["spec"]["chart_type"] == "line"


def test_explicit_line_chart_uses_monthly_revenue_without_asking(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Line chart"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={
                "uploads": (
                    "monthly_revenue.csv",
                    b"Month,Revenue\n2026-01,100\n2026-02,125\n2026-03,160\n2026-04,155\n",
                    "text/csv",
                )
            },
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Create a line chart of monthly revenue"})

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert run["current_question"] is None
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        chart = messages[-1]["artifacts"][0]
        assert chart["kind"] == "chart"
        assert chart["spec"]["chart_type"] == "line"
        assert chart["spec"]["x_label"] == "Month"
        assert chart["spec"]["y_label"] == "Revenue"
        assert chart["spec"]["source_columns"] == ["Month", "Revenue"]
        assert [point["label"] for point in chart["spec"]["values"]] == ["2026-01", "2026-02", "2026-03", "2026-04"]


def test_analysis_engine_excludes_sku_and_ranks_regional_forecast_trend():
    path = Path("test_documents/correlated_business/regional_demand_forecast.csv")
    text = path.read_text(encoding="utf-8")
    file_texts = [{"file_id": "fil_forecast", "file_name": path.name, "text": text}]
    sources = [{"source_id": 1, "file_id": "fil_forecast", "file_name": path.name, "chunk_id": "chk_forecast"}]

    brief = build_insight_brief("best chart for this file", file_texts, sources)

    top = brief["insights"][0]
    assert top["framework"] == "trend"
    assert top["recommended_visual"]["visual_form"] == "line"
    assert top["evidence"]["x_column"] == "forecast_month"
    assert top["evidence"]["y_column"] == "forecast_units"
    assert top["evidence"]["values"][0]["label"] == "2026-05"
    assert all("SKU" not in fact.get("column", "") for fact in brief["metric_catalog"]["volumes"])
    assert any(item["column"] == "SKU" for item in brief["metric_catalog"]["identifiers_excluded"])


def test_regional_demand_forecast_best_chart_uses_forecast_month_not_sku(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Forecast"}).json()
        body = Path("test_documents/correlated_business/regional_demand_forecast.csv").read_bytes()
        uploaded = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("regional_demand_forecast.csv", body, "text/csv")},
        ).json()[0]

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "best chart for this file"})

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert any(call["tool"] == "source_profiler" for call in run["tool_calls"])
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        chart = messages[-1]["artifacts"][0]
        assert chart["kind"] == "chart"
        assert chart["spec"]["chart_type"] == "line"
        assert chart["spec"]["x_column"] == "forecast_month"
        assert chart["spec"]["y_column"] == "forecast_units"
        assert "SKU" not in chart["spec"]["source_columns"]
        narrative = chart["spec"]["insight_narrative"]
        narrative_text = json.dumps(narrative)
        assert "forecast_month" in narrative_text
        assert "forecast_units" in narrative_text
        assert "Aggregated values can hide row-level variation." in narrative_text
        assert "Inspect the largest segment before acting." in narrative["recommended_actions"]
        assert narrative["confidence"] in {"medium", "high"}
        assert narrative["source_columns"] == ["forecast_month", "forecast_units"]

        export = client.get(f"/api/sessions/{session['id']}/artifacts/{chart['id']}/export?format=md")
        assert export.status_code == 200
        assert "## Meaning" in export.text
        assert "Inspect the largest segment before acting." in export.text

        notion_export = client.get(f"/api/sessions/{session['id']}/artifacts/{chart['id']}/export?format=notion")
        assert notion_export.status_code == 200
        notion_payload = notion_export.json()
        assert "Inspect the largest segment before acting." in notion_payload["markdown"]
        assert "Aggregated values can hide row-level variation." in notion_payload["markdown"]
        assert notion_payload["datatable"]["columns"] == ["Label", "Value"]

        csv_export = client.get(f"/api/sessions/{session['id']}/artifacts/{chart['id']}/export?format=csv")
        assert csv_export.status_code == 200
        assert "Label,Value" in csv_export.text
        assert "2026-05" in csv_export.text


def test_chart_validation_preserves_insight_narrative():
    report = validate_artifacts_with_report(
        [
            {
                "kind": "chart",
                "title": "Forecast trend",
                "source_ids": [1],
                "chart_type": "line",
                "x_label": "forecast_month",
                "y_label": "forecast_units",
                "source_columns": ["forecast_month", "forecast_units"],
                "values": [{"label": "2026-05", "value": 100, "source_id": 1}],
                "insight_narrative": {
                    "headline": "Forecast units are rising",
                    "meaning": "The x-axis uses forecast_month and the measure is aggregated forecast_units.",
                    "evidence": ["forecast_month orders the trend.", "aggregated forecast_units is the measure."],
                    "so_what": "Treat this as a demand planning view.",
                    "recommended_actions": ["Inspect region/SKU mix before action."],
                    "follow_up_questions": [
                        {
                            "id": "q_mix",
                            "group": "data",
                            "question": "Which region/SKU combinations explain the trend?",
                            "options": [{"id": "inspect", "label": "Inspect mix"}],
                            "default_option": "inspect",
                            "requires_reference": True,
                        }
                    ],
                    "caveats": ["SKU is an identifier/dimension, not a measure."],
                    "confidence": "high",
                    "source_columns": ["forecast_month", "forecast_units"],
                },
            }
        ],
        [{"source_id": 1, "chunk_id": "chk_1"}],
    )

    assert report.warnings == []
    assert report.artifacts[0].spec["insight_narrative"]["headline"] == "Forecast units are rising"


def test_artifact_discovery_uses_inventory_recommendations(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Inventory options"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={
                "uploads": (
                    "warehouse_stock_units.csv",
                    b"SKU,warehouse,category,units_on_hand,reorder_point,unit_cost\nHB-100,North,Hardware,18,30,12.50\nHB-200,South,Hardware,90,40,8.25\n",
                    "text/csv",
                )
            },
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "what charts and docs can you make with this?"})

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        cards = messages[-1]["artifacts"][0]
        assert cards["kind"] == "decision_cards"
        text = json.dumps(cards["spec"], ensure_ascii=False)
        assert "Record comparison" not in text
        assert "table" in text.lower()
        assert "SKU" in text
        assert "survey" not in text.lower()


def test_sales_order_chart_discovery_does_not_red_team_fail_on_stale_bundle_contract(monkeypatch, tmp_path):
    async def fake_review_phase(
        self,
        *,
        model,
        phase,
        phase_goal,
        task_contract,
        evidence_packet,
        source_refs,
        artifact_specs,
        answer_draft,
        prior_checker_reports=None,
    ):
        executable = task_contract.get("executable_contract") if isinstance(task_contract.get("executable_contract"), dict) else {}
        stale_required = set(executable.get("required_outputs") or task_contract.get("required_outputs") or [])
        stale_findings = [
            finding
            for report in prior_checker_reports or []
            for finding in report.get("findings", [])
            if "Missing requested artifact" in str(finding)
        ]
        if stale_required & {"chart", "file_draft"} or stale_findings:
            return {
                "phase": phase,
                "passed": False,
                "severity": "high",
                "findings": ["Discovery-only decision cards were reviewed against stale production outputs."],
                "required_fixes": ["Review chart discovery against decision_cards only."],
                "suggested_followups": [],
                "confidence": "high",
            }
        return {
            "phase": phase,
            "passed": True,
            "severity": "none",
            "findings": [],
            "required_fixes": [],
            "suggested_followups": [],
            "confidence": "high",
        }

    monkeypatch.setattr(OpenRouterClient, "review_phase", fake_review_phase)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Sales order chart discovery"}).json()
        body = Path("test_documents/correlated_business/sales_orders.csv").read_bytes()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("sales_orders.csv", body, "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "what charts can we make from this file?"})

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert run["task_contract"]["required_outputs"] == ["decision_cards"]
        assert run["task_contract"]["executable_contract"]["required_outputs"] == ["decision_cards"]
        artifact_check = next(item for item in client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json() if item["path"] == "/review/artifact-check.json")
        assert artifact_check["content"]["severity"] != "high"
        cards = client.get(f"/api/sessions/{session['id']}/messages").json()[-1]["artifacts"][0]
        assert cards["kind"] == "decision_cards"
        descriptions = [
            str(option.get("description") or "")
            for option in cards["spec"].get("decision_options", [])
        ]
        assert descriptions
        assert all("segment-level variation" in description for description in descriptions)


def test_writer_receives_evidence_packet_not_global_context(monkeypatch, tmp_path):
    captured: dict[str, dict] = {}

    async def fake_build_artifacts(
        self,
        *,
        model,
        question,
        artifact_plan,
        source_profile,
        sources,
        prompt_context,
        reasoning_effort="none",
    ):
        captured["prompt_context"] = prompt_context
        captured["source_profile"] = source_profile
        captured["artifact_plan"] = artifact_plan
        return ChatResult(
            answer="Built from source profile.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "file_draft",
                    "title": "Survey Draft",
                    "caption": "Evidence-grounded draft.",
                    "source_ids": [1],
                    "filename": "survey-draft.md",
                    "format": "markdown",
                    "content": "# Survey Draft\n\n- Yes: 10",
                }
            ],
            model=model,
        )

    monkeypatch.setattr(OpenRouterClient, "build_artifacts", fake_build_artifacts)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Writer packet"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("survey.csv", b"Answer,Count\nYes,10\nNo,4", "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Make a chart and draft about the survey result"})

        assert captured["prompt_context"]["packet_kind"] == "writer"
        assert captured["source_profile"]["tables"]
        assert "file_intelligence" not in captured["prompt_context"]
        assert captured["artifact_plan"]["artifacts"]


def test_deep_routing_preflight_waits_for_approval(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        set_setting("model_routing_mode", "deep")
        session = client.post("/api/sessions", json={"title": "Approval"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("run.csv", b"Answer,Count\nYes,10\nNo,4", "text/csv")},
        )

        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Make a chart about the survey result"})

        assert started.status_code == 200
        assert started.json()["status"] == "awaiting_approval"
        approved = client.post(f"/api/sessions/{session['id']}/runs/{started.json()['id']}/approve-plan")
        assert approved.status_code == 200
        runs = client.get(f"/api/sessions/{session['id']}/runs").json()
        assert runs[0]["status"] == "completed"


def test_broad_korean_create_request_offers_interview_or_automatic(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Planning question"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("Form Responses 1.csv", "Answer,Count\n예,10\n아니오,4\n".encode("utf-8"), "text/csv")},
        )

        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "분석 자료 제작"})

        assert started.status_code == 200
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "awaiting_user_input"
        assert run["current_question"]["kind"] == "interview_offer"
        assert [option["id"] for option in run["current_question"]["options"]] == ["automatic", "interview"]
        current = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/questions/current").json()
        assert current["id"] == run["current_question"]["id"]
        events = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/events").json()
        assert any(event["type"] == "question_created" for event in events)
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        assert any(item["path"] == "/plan/ambiguity.json" for item in workspace)
        assert any(item["path"] == "/plan/task-contract.json" for item in workspace)


def test_run_output_separates_blocking_and_follow_up_questions(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Question contracts"}).json()
        run = create_agent_run(session["id"], "Make the best chart")
        blocking = create_run_question(
            run.id,
            action_kind="ask_user",
            kind="clarification",
            question="Which audience is this for?",
            options=[{"id": "exec", "label": "Executives", "description": ""}],
            default_option="exec",
            blocking=True,
            phase="plan_check",
            card={
                "title": "Audience",
                "prompt": "Choose the audience.",
                "group": "business",
                "options": [{"id": "exec", "label": "Executives"}],
                "allow_free_text": True,
                "allow_file_reference": False,
            },
        )
        follow_up = create_run_question(
            run.id,
            action_kind="write",
            kind="choice",
            question="Validate allocation assumptions?",
            options=[{"id": "validate", "label": "Validate", "description": "Check assumptions first."}],
            default_option="validate",
            blocking=False,
            phase="artifact_check",
            card={
                "title": "Next move",
                "prompt": "Validate allocation assumptions?",
                "group": "business",
                "options": [{"id": "validate", "label": "Validate"}],
                "allow_free_text": True,
                "allow_file_reference": True,
            },
            parent_message_id="msg_parent",
            parent_artifact_id="art_parent",
        )

        payload = client.get(f"/api/sessions/{session['id']}/runs/{run.id}").json()
        current = client.get(f"/api/sessions/{session['id']}/runs/{run.id}/questions/current").json()

        assert payload["current_question"]["id"] == blocking.id
        assert current["id"] == blocking.id
        assert [question["id"] for question in payload["follow_up_questions"]] == [follow_up.id]
        assert payload["follow_up_questions"][0]["blocking"] is False
        assert payload["follow_up_questions"][0]["phase"] == "artifact_check"
        assert payload["follow_up_questions"][0]["card"]["allow_file_reference"] is True
        assert payload["follow_up_questions"][0]["parent_message_id"] == "msg_parent"
        assert payload["follow_up_questions"][0]["parent_artifact_id"] == "art_parent"


def test_non_blocking_question_does_not_become_current_question(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Follow-up only"}).json()
        run = create_agent_run(session["id"], "Make the best chart")
        question = create_run_question(
            run.id,
            action_kind="write",
            kind="choice",
            question="Inspect SKU mix?",
            options=[{"id": "inspect", "label": "Inspect mix", "description": ""}],
            blocking=False,
            phase="writing_check",
            card={
                "title": "Question to answer next",
                "prompt": "Inspect SKU mix?",
                "group": "data",
                "options": [{"id": "inspect", "label": "Inspect mix"}],
                "allow_free_text": True,
                "allow_file_reference": True,
            },
        )

        payload = client.get(f"/api/sessions/{session['id']}/runs/{run.id}").json()
        current = client.get(f"/api/sessions/{session['id']}/runs/{run.id}/questions/current")

        assert current.status_code == 200
        assert current.json() is None
        assert payload["current_question"] is None
        assert [item["id"] for item in payload["follow_up_questions"]] == [question.id]


def test_decision_card_artifact_creates_multi_select_artifact_choice(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Artifact choice"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("sales.csv", b"Month,Revenue\nJan,10\nFeb,15\nMar,21\n", "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "what charts and docs can you make with this?"})
        parent = client.get(f"/api/sessions/{session['id']}/runs").json()[0]

        assert parent["status"] == "completed"
        follow_up = parent["follow_up_questions"][0]
        assert follow_up["kind"] == "artifact_choice"
        assert follow_up["blocking"] is False
        assert follow_up["phase"] == "artifact_choice"
        assert follow_up["card"]["allow_multi_select"] is True
        assert follow_up["card"]["submit_label"] == "Produce selected"
        assert follow_up["card"]["allow_free_text"] is False
        assert len(follow_up["options"]) >= 1
        assert all(option["produce_payload"] for option in follow_up["card"]["options"])


def test_broad_korean_create_request_automatic_resume_builds_artifacts(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Automatic planning"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("Form Responses 1.csv", "Answer,Count\n예,10\n아니오,4\n".encode("utf-8"), "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "분석 자료 제작"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        answered = answer_current_question(client, session["id"], run, "automatic")

        assert answered.status_code == 200
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert "prompt_context" not in run
        assert run["current_question"] is None
        assert any(call["tool"] == "source_profiler" for call in run["tool_calls"])
        assert any(action["kind"] == "profile_table" for action in run["actions"])
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        artifacts = messages[-1]["artifacts"]
        assert {artifact["kind"] for artifact in artifacts} >= {"chart", "table", "file_draft"}
        chart = next(artifact for artifact in artifacts if artifact["kind"] == "chart")
        table = next(artifact for artifact in artifacts if artifact["kind"] == "table")
        draft = next(artifact for artifact in artifacts if artifact["kind"] == "file_draft")
        assert chart["display_mode"] == "primary"
        assert draft["display_mode"] == "primary"
        assert table["display_mode"] == "primary"
        assert chart["title"] != "Survey themes"
        assert draft["spec"]["filename"] != "analysis-material.md"
        assert not draft["spec"]["content"].startswith("# 분석 자료")
        assert chart["spec"]["values"][0]["label"] == "예"
        assert chart["spec"]["values"][0]["value"] == 10
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        inferred_plan = next(item for item in workspace if item["path"] == "/plan/inferred-plan.json")
        assert inferred_plan["content"]["selected_mode"] == "automatic"


def test_summary_panel_planner_request_is_reconciled_and_run_completes(monkeypatch, tmp_path):
    async def fake_plan_task(self, *, model, question, file_manifest, prior_answers=None, prompt_context=None, reasoning_effort="none"):
        return {
            "intent": "create",
            "deliverable": "각 사 워크샵 설계 자료",
            "language": "ko",
            "required_outputs": ["summary_panel", "chart", "file_draft"],
            "analysis_focus": ["themes", "evidence", "workshop design"],
            "success_criteria": ["grounded materials", "usable result"],
            "needs_user_question": False,
            "user_question": "",
            "question_options": [],
            "default_option": "",
        }

    monkeypatch.setattr(OpenRouterClient, "plan_task", fake_plan_task)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Capability reconciliation"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("Form Responses 1.csv", "Answer,Count\n예,10\n아니오,4\n".encode("utf-8"), "text/csv")},
        )

        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "각 사 워크샵 설계 자료 제작"})

        assert started.status_code == 200
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert run["task_contract"]["planner_contract"]["required_outputs"] == ["summary_panel", "chart", "file_draft"]
        assert run["task_contract"]["executable_contract"]["primary_outputs"] == ["file_draft", "chart"]
        assert run["task_contract"]["executable_contract"]["supporting_outputs"] == ["summary_panel"]
        assert any("summary_panel" in adjustment.lower() for adjustment in run["task_contract"]["contract_adjustments"])
        assert run["review_scores"]["passed"] is True
        persist = next(action for action in run["actions"] if action["kind"] == "persist_response")
        assert persist["status"] == "completed"
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        summary_panel = next(artifact for artifact in messages[-1]["artifacts"] if artifact["kind"] == "summary_panel")
        assert summary_panel["display_mode"] == "supporting"


def test_explicit_summary_panel_request_is_synthesized_from_evidence(monkeypatch, tmp_path):
    async def fake_plan_task(self, *, model, question, file_manifest, prior_answers=None, prompt_context=None, reasoning_effort="none"):
        return {
            "intent": "create",
            "deliverable": "insight_report",
            "language": "ko",
            "required_outputs": ["summary_panel", "chart", "file_draft"],
            "analysis_focus": ["themes", "evidence"],
            "success_criteria": ["grounded materials", "summary ready"],
            "needs_user_question": False,
            "user_question": "",
            "question_options": [],
            "default_option": "",
        }

    monkeypatch.setattr(OpenRouterClient, "plan_task", fake_plan_task)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Summary synthesis"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("Form Responses 1.csv", "Answer,Count\n예,10\n아니오,4\n".encode("utf-8"), "text/csv")},
        )

        started = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "요약 패널 포함해서 분석 자료 제작"})

        assert started.status_code == 200
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        events = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/events").json()
        assert any(event["type"] == "artifact_synthesized" for event in events)
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        summary_panel = next(artifact for artifact in messages[-1]["artifacts"] if artifact["kind"] == "summary_panel")
        assert summary_panel["display_mode"] == "supporting"
        assert summary_panel["spec"]["root"] == "card"


def test_review_rejects_generic_repetitive_analysis_draft():
    review = review_contract_result(
        task_contract={"required_outputs": ["file_draft"], "deliverable": "insight_report"},
        answer="Draft created.",
        artifacts=[
            ValidatedArtifact(
                kind="file_draft",
                title="분석 자료 초안",
                caption="",
                source_chunk_ids=["chk_1"],
                spec={
                    "filename": "analysis-material.md",
                    "format": "markdown",
                    "content": "# 분석 자료\n\n- Column: open_text, non-empty 31, unique 31\n작성 팁: raw prompt",
                },
            )
        ],
        cited_source_ids=[1],
    )

    assert review["passed"] is False
    assert any("generic title" in failure for failure in review["failures"])
    assert any("raw survey metadata" in failure for failure in review["failures"])


def test_review_rejects_instruction_only_file_draft():
    review = review_contract_result(
        task_contract={"required_outputs": ["file_draft"], "deliverable": "insight_report"},
        answer="I created the draft.",
        artifacts=[
            ValidatedArtifact(
                kind="file_draft",
                title="Customer Survey Improvement Plan",
                caption="",
                source_chunk_ids=["chk_1"],
                spec={
                    "filename": "customer-survey-improvement-plan.md",
                    "format": "markdown",
                    "content": (
                        "# Customer Survey Improvement Plan\n\n"
                        "To make the source document better, add an executive summary, clean up the formatting, "
                        "remove duplicated rows, improve the section headings, add charts, clarify the audience, "
                        "and rewrite weak paragraphs. You should also check every claim against the original source."
                    ),
                },
            )
        ],
        cited_source_ids=[1],
    )

    assert review["passed"] is False
    assert any("instruction" in failure.lower() for failure in review["failures"])


def test_instruction_only_model_draft_does_not_replace_deterministic_draft():
    deterministic = [
        {"kind": "chart", "title": "Survey themes", "values": [{"label": "A", "value": 1}]},
        {
            "kind": "file_draft",
            "title": "Real source analysis",
            "filename": "real-source-analysis.md",
            "content": "# Real source analysis\n\nThis is the actual generated document body from deterministic evidence.",
        },
    ]
    model_draft = {
        "kind": "file_draft",
        "title": "How to improve the source document",
        "filename": "source-document-instructions.md",
        "content": "To make the source document better, add a summary, rewrite weak areas, and improve formatting.",
    }

    replaced = _replace_draft_artifact(deterministic, model_draft)

    draft = next(artifact for artifact in replaced if artifact["kind"] == "file_draft")
    assert draft["title"] == "Real source analysis"


def test_review_allows_missing_supporting_artifact_with_warning():
    review = review_contract_result(
        task_contract={
            "required_outputs": ["file_draft", "chart"],
            "primary_outputs": ["file_draft", "chart"],
            "supporting_outputs": ["summary_panel"],
            "deliverable": "insight_report",
        },
        answer="분석 자료를 만들었습니다.",
        artifacts=[
            ValidatedArtifact(
                kind="file_draft",
                title="고통 지수 설문: 분석 초안",
                caption="",
                source_chunk_ids=["chk_1"],
                spec={"filename": "analysis.md", "format": "markdown", "content": "# 고통 지수 설문: 분석 초안\n\n충분한 분석 본문입니다." * 40},
            ),
            ValidatedArtifact(
                kind="chart",
                title="고통 지수 설문: 응답 주제 분포",
                caption="",
                source_chunk_ids=["chk_1"],
                spec={"chart_type": "bar", "x_label": "주제", "y_label": "응답 수", "values": [{"label": "예", "value": 10}]},
            ),
        ],
        cited_source_ids=[1],
    )

    assert review["passed"] is True
    assert review["failures"] == []
    assert any("supporting artifact" in warning.lower() for warning in review["warnings"])


def test_survey_timestamp_column_cannot_be_used_as_chart_measure(monkeypatch, tmp_path):
    csv_body = (
        "Timestamp,Email Address,소모적인 작업은 무엇인가요?\n"
        "3/20/2026 19:10:37,a@example.com,원고 검토와 교정 확인이 오래 걸립니다.\n"
        "3/20/2026 19:11:09,b@example.com,자료 검색과 레퍼런스 정리가 어렵습니다.\n"
        "3/20/2026 19:12:27,c@example.com,반복 검수와 피드백 정리가 부담됩니다.\n"
    ).encode("utf-8")

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Timestamp survey"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("Form Responses 1.csv", csv_body, "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "분석 자료 제작"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        answer_current_question(client, session["id"], run, "automatic")

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        assert run["review_scores"]["passed"] is True
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        chart = next(artifact for artifact in messages[-1]["artifacts"] if artifact["kind"] == "chart")
        assert chart["spec"]["y_label"] == "Count"
        assert all(value["value"] < 100 for value in chart["spec"]["values"])
        assert all(len(value["label"]) < 80 for value in chart["spec"]["values"])


def test_upload_csv_stays_ready_when_embedding_auth_fails(monkeypatch, tmp_path):
    async def failed_embedding_result(self, inputs, model):
        raise openrouter_401()

    monkeypatch.setattr(OpenRouterClient, "embedding_result", failed_embedding_result)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Degraded indexing"}).json()
        upload = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("Form Responses 1.csv", "Answer,Count\n예,10\n아니오,4\n".encode("utf-8"), "text/csv")},
        )

        assert upload.status_code == 200
        files = client.get(f"/api/sessions/{session['id']}/files").json()
        assert files[0]["status"] == "ready"
        assert files[0]["chunk_count"] >= 1
        assert "OpenRouter authentication failed" in files[0]["error"]


def test_broad_korean_create_request_query_embedding_401_uses_local_artifacts(monkeypatch, tmp_path):
    embedding_calls = 0

    async def flaky_embedding_result(self, inputs, model):
        nonlocal embedding_calls
        embedding_calls += 1
        if embedding_calls > 1:
            raise openrouter_401()
        return EmbeddingResult(vectors=[[1.0, 0.0] for _ in inputs], model=model, usage=UsageInfo())

    monkeypatch.setattr(OpenRouterClient, "embedding_result", flaky_embedding_result)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Query embedding degraded"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("Form Responses 1.csv", "Answer,Count\n예,10\n아니오,4\n".encode("utf-8"), "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "분석 자료 제작"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        answer_current_question(client, session["id"], run, "automatic")

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        search = next(action for action in run["actions"] if action["kind"] == "load_sources")
        assert search["status"] == "completed"
        assert search["output_json"]["vector_search_status"] == "unavailable_auth"
        events = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/events").json()
        assert any(event["type"] == "tool_failed" and event["detail"]["tool"] == "embedding_search" for event in events)
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        assert "401 Unauthorized" not in messages[-1]["content"]
        assert "OpenRouter key needs attention" in messages[-1]["content"]
        assert {artifact["kind"] for artifact in messages[-1]["artifacts"]} >= {"chart", "table", "file_draft"}


def test_broad_korean_create_request_interview_resume_stores_clarification(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Interview planning"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("Form Responses 1.csv", "Answer,Count\n예,10\n아니오,4\n".encode("utf-8"), "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "분석 자료 제작"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["current_question"]["kind"] == "interview_offer"
        answer_current_question(client, session["id"], run, "interview")

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "awaiting_user_input"
        assert run["current_question"]["kind"] == "clarification"
        assert [option["id"] for option in run["current_question"]["options"]] == ["leadership_report", "team_workshop", "data_review"]
        answer_current_question(client, session["id"], run, "leadership_report", "팀장에게 공유할 자료")

        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        assert run["status"] == "completed"
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        clarification = next(item for item in workspace if item["path"] == "/plan/user-clarification.json")
        assert "leadership_report" in clarification["content"]["clarification"]
        assert "팀장" in clarification["content"]["clarification"]
        assert run["task_contract"]["user_direction"]["selected_option"] == "leadership_report"


def test_valid_chart_artifact_is_typed_and_returned(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="차트를 만들었습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "chart",
                    "title": "Survey result",
                    "caption": "문서 근거 기반 차트",
                    "source_ids": [1],
                    "chart_type": "bar",
                    "x_label": "Answer",
                    "y_label": "Count",
                    "values": [{"label": "Yes", "value": 10, "source_id": 1}, {"label": "No", "value": 4, "source_id": 1}],
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Chart"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("survey.csv", b"Answer,Count\nYes,10\nNo,4", "text/csv")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Make a chart"})

        artifact = answer.json()["artifacts"][0]
        assert artifact["kind"] == "chart"
        assert artifact["spec"]["values"][0]["label"] == "Yes"
        assert artifact["spec"]["values"][0]["value"] == 10


def test_chart_artifact_accepts_numeric_strings_and_infers_sources(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="차트를 만들었습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "chart",
                    "title": "Survey result",
                    "values": [{"category": "Yes", "value": "1,234", "source_id": 1}, {"category": "No", "value": "12%", "source_id": 1}],
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Chart strings"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("survey.csv", b"Answer,Count\nYes,1234\nNo,12", "text/csv")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Make a chart"})

        values = answer.json()["artifacts"][0]["spec"]["values"]
        assert values[0]["value"] == 1234
        assert values[1]["value"] == 12


def test_invalid_chart_artifact_uses_deterministic_fallback(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="차트를 만들었습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "chart",
                    "title": "Broken chart",
                    "source_ids": [1],
                    "values": [{"label": "Yes", "value": "not-a-number", "source_id": 1}],
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Invalid Chart"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("survey.csv", b"Answer,Count\nYes,10", "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Make a chart"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        review = next(action for action in run["actions"] if action["kind"] == "validate")

        assert review["output_json"].get("warnings", []) == []
        assert run["repair_attempts"] == []
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        assert messages[-1]["artifacts"][0]["kind"] == "chart"
        assert "could not render" not in messages[-1]["content"]


def test_schema_valid_but_semantically_bad_chart_needs_revision(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="차트를 만들었습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "chart",
                    "title": "Bad timestamp chart",
                    "source_ids": [1],
                    "chart_type": "bar",
                    "x_label": "Response",
                    "y_label": "Timestamp",
                    "values": [{"label": "A very long free text answer", "value": 3202026191037, "source_id": 1}],
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Bad semantic chart"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("memo.txt", b"Source text is available but contains no usable table.", "text/plain")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Make a chart"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]

        assert run["status"] == "needs_revision"
        assert run["revision_required"] is True
        assert run["review_scores"]["passed"] is False
        assert "timestamp" in " ".join(run["review_scores"]["failures"]).lower()


def test_file_draft_artifact_exports_markdown_and_json(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="초안을 만들었습니다.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "file_draft",
                    "title": "Memo draft",
                    "caption": "문서 기반 초안",
                    "source_ids": [1],
                    "filename": "memo.md",
                    "format": "markdown",
                    "content": "# Memo\n\nGrounded draft.",
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Draft"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("memo.txt", b"Grounded draft source.", "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Create a new file"})
        artifact = answer.json()["artifacts"][0]

        md = client.get(f"/api/sessions/{session['id']}/artifacts/{artifact['id']}/export?format=md")
        js = client.get(f"/api/sessions/{session['id']}/artifacts/{artifact['id']}/export?format=json")

        assert md.status_code == 200
        assert "# Memo" in md.text
        assert js.status_code == 200
        assert js.json()["content"] == "# Memo\n\nGrounded draft."


def test_file_draft_artifact_exports_open_design_zip(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="Built an Open Design compatible draft.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "file_draft",
                    "title": "Open Design Brief",
                    "caption": "Open Design ZIP ready",
                    "source_ids": [1],
                    "filename": "brief.md",
                    "format": "markdown",
                    "content": "# Brief\n\nGrounded launch material.",
                    "open_design": {"material_type": "design_brief", "skill_name": "Launch Brief"},
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Open Design draft"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("memo.txt", b"Launch material source.", "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Create an Open Design design brief"})
        artifact = answer.json()["artifacts"][0]
        export = client.get(f"/api/sessions/{session['id']}/artifacts/{artifact['id']}/export?format=od")

        assert export.status_code == 200
        assert export.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(export.content)) as bundle:
            assert set(bundle.namelist()) == {"SKILL.md", "DESIGN.md", "content.md", "metadata.json"}
            metadata = json.loads(bundle.read("metadata.json"))
        assert metadata["material_type"] == "design_brief"
        assert metadata["source_artifact_id"] == artifact["id"]


def test_open_design_export_rejects_non_eligible_artifacts(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="Built a normal draft.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "file_draft",
                    "title": "Normal Draft",
                    "source_ids": [1],
                    "filename": "normal.md",
                    "format": "markdown",
                    "content": "# Normal",
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Normal draft"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("memo.txt", b"Normal source.", "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Create a normal file"})
        artifact = answer.json()["artifacts"][0]
        export = client.get(f"/api/sessions/{session['id']}/artifacts/{artifact['id']}/export?format=od")

        assert export.status_code == 400
        assert "Open Design" in export.json()["detail"]


def test_file_draft_artifact_exports_notion_bundle(monkeypatch, tmp_path):
    async def fake_chat(self, *, model, question, sources, unavailable, history=None):
        return ChatResult(
            answer="Built a Notion-ready draft.",
            cited_source_ids=[1],
            artifacts=[
                {
                    "kind": "file_draft",
                    "title": "Notion Memo",
                    "caption": "Import-ready",
                    "source_ids": [1],
                    "filename": "notion-memo.md",
                    "format": "markdown",
                    "content": "# Notion Memo\n\n| SKU | Action |\n| --- | --- |\n| HB-100 | Reorder |",
                }
            ],
            model=model,
        )

    monkeypatch.setattr("backend.app.retrieval.OpenRouterClient.chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Notion draft"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("memo.txt", b"HB-100 needs reorder.", "text/plain")},
        )

        answer = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Create a Notion document"})
        artifact = answer.json()["artifacts"][0]

        notion = client.get(f"/api/sessions/{session['id']}/artifacts/{artifact['id']}/export?format=notion")

        assert notion.status_code == 200
        payload = notion.json()
        assert payload["metadata"]["source_artifact_id"] == artifact["id"]
        assert payload["metadata"]["source_chunk_ids"]
        assert "# Notion Memo" in payload["markdown"]
        assert payload["datatable"]["columns"] == ["SKU", "Action"]
        assert "HB-100,Reorder" in payload["datatable"]["csv"]


def test_agent_run_persists_phase_checker_reports(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Checker reports"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("survey.csv", b"Answer,Count\nYes,10\nNo,4", "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Make a chart"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        reports = {item["path"]: item["content"] for item in workspace if item["path"].startswith("/review/")}

        assert reports["/review/plan-check.json"]["phase"] == "plan_check"
        assert reports["/review/source-check.json"]["passed"] is True
        assert reports["/review/analysis-check.json"]["phase"] == "analysis_check"
        assert reports["/review/artifact-check.json"]["passed"] is True
        assert reports["/review/writing-check.json"]["passed"] is True
        assert reports["/review/proofread.json"]["reviewed_output"]["answer"]


def test_planner_and_writer_receive_required_context_packets(monkeypatch, tmp_path):
    captured: dict[str, dict] = {}

    async def fake_plan_task(self, **kwargs):
        captured["plan"] = kwargs
        return {
            "intent": "ask",
            "deliverable": "answer",
            "language": "en",
            "required_outputs": ["answer"],
            "success_criteria": ["grounded answer"],
            "needs_user_question": False,
        }

    async def fake_chat(self, **kwargs):
        captured["writer"] = kwargs
        return ChatResult(answer="The memo says HB-100 needs reorder.", cited_source_ids=[1])

    monkeypatch.setattr(OpenRouterClient, "plan_task", fake_plan_task)
    monkeypatch.setattr(OpenRouterClient, "chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Prompt context"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("memo.txt", b"HB-100 needs reorder next week.", "text/plain")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Summarize this file"})

        plan_context = captured["plan"]["prompt_context"]
        assert captured["plan"]["question"] == "Summarize this file"
        assert captured["plan"]["file_manifest"][0]["name"] == "memo.txt"
        assert isinstance(captured["plan"]["prior_answers"], list)
        assert plan_context["current_request"] == "Summarize this file"
        assert plan_context["file_manifest"][0]["name"] == "memo.txt"
        assert "conversation_tail" in plan_context
        assert "user_preferences" in plan_context

        writer_context = captured["writer"]["prompt_context"]
        assert captured["writer"]["sources"][0]["file_name"] == "memo.txt"
        assert writer_context["packet_kind"] == "writer"
        assert writer_context["output_contract"]["required_outputs"] == ["answer"]
        assert "selected_artifact_recommendation" in writer_context["output_contract"]
        assert "evidence_packet" in writer_context


def test_review_and_proofread_receive_required_context_packets(monkeypatch, tmp_path):
    captured: dict[str, dict] = {}

    async def fake_review_phase(self, **kwargs):
        captured["review"] = kwargs
        return {
            "phase": kwargs["phase"],
            "passed": True,
            "severity": "none",
            "findings": [],
            "required_fixes": [],
            "suggested_followups": [],
            "confidence": "high",
        }

    async def fake_proofread_output(self, **kwargs):
        captured["proofread"] = kwargs
        return {"answer": kwargs["answer_draft"], "insight_narrative": kwargs.get("insight_narrative") or {}}

    monkeypatch.setattr(OpenRouterClient, "review_phase", fake_review_phase)
    monkeypatch.setattr(OpenRouterClient, "proofread_output", fake_proofread_output)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Review context"}).json()
        body = Path("test_documents/correlated_business/regional_demand_forecast.csv").read_bytes()
        uploaded = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("regional_demand_forecast.csv", body, "text/csv")},
        ).json()[0]

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "best chart for this file"})

        review = captured["review"]
        assert review["phase_goal"]
        assert review["task_contract"]["required_outputs"] == ["chart"]
        assert review["source_refs"][0]["file_name"] == "regional_demand_forecast.csv"
        assert review["artifact_specs"][0]["spec"]["insight_narrative"]["source_columns"] == ["forecast_month", "forecast_units"]
        assert review["answer_draft"]
        assert review["evidence_packet"]["source_profile"]["tables"]
        assert review["evidence_packet"]["artifact_plan"]["artifacts"]

        proofread = captured["proofread"]
        assert proofread["answer_draft"]
        assert proofread["insight_narrative"]["source_columns"] == ["forecast_month", "forecast_units"]
        assert "evidence_packet" in proofread
        assert isinstance(proofread["red_team_findings"], list)


def test_high_severity_red_team_blocks_persistence(monkeypatch, tmp_path):
    async def fake_review_phase(self, **kwargs):
        return {
            "phase": kwargs["phase"],
            "passed": False,
            "severity": "high",
            "findings": ["Unsupported causal claim."],
            "required_fixes": ["Remove the causal claim."],
            "suggested_followups": [],
            "confidence": "high",
        }

    monkeypatch.setattr(OpenRouterClient, "review_phase", fake_review_phase)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "High severity review"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("survey.csv", b"Answer,Count\nYes,10\nNo,4", "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Make a chart"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]

        assert run["status"] == "failed"
        assert run["revision_required"] is False
        assert run["assistant_message_id"]
        message = client.get(f"/api/sessions/{session['id']}/messages").json()[-1]
        assert "could not generate the requested artifacts safely" in message["content"]
        assert message["artifacts"] == []
        assert run["repair_attempts"][0]["strategy"] == "artifact_engine_repair"
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        red_team = next(item for item in workspace if item["path"] == "/review/red-team.json")
        assert red_team["content"]["severity"] == "high"


def test_proofread_output_is_persisted_instead_of_raw_draft(monkeypatch, tmp_path):
    async def fake_proofread_output(self, **kwargs):
        return {
            "answer": "Reviewed chart answer.",
            "insight_narrative": kwargs.get("insight_narrative") or {},
        }

    monkeypatch.setattr(OpenRouterClient, "proofread_output", fake_proofread_output)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Proofread output"}).json()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("survey.csv", b"Answer,Count\nYes,10\nNo,4", "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Make a chart"})
        run = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        proofread = next(item for item in workspace if item["path"] == "/review/proofread.json")

        assert run["status"] == "completed"
        assert messages[-1]["content"] == "Reviewed chart answer."
        assert proofread["content"]["reviewed_output"]["answer"] == "Reviewed chart answer."


def test_answering_follow_up_question_creates_linked_child_run(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Forecast follow-up"}).json()
        body = Path("test_documents/correlated_business/regional_demand_forecast.csv").read_bytes()
        uploaded = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("regional_demand_forecast.csv", body, "text/csv")},
        ).json()[0]

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "best chart for this file"})
        parent = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        follow_up = parent["follow_up_questions"][0]

        response = client.post(
            f"/api/sessions/{session['id']}/runs/{parent['id']}/questions/{follow_up['id']}/answer",
            json={
                "selected_option": "inspect_mix",
                "free_text": "Focus on West region.",
                "attached_file_ids": [uploaded["id"]],
            },
        )

        assert response.status_code == 200
        child = response.json()
        assert child["id"] != parent["id"]
        assert child["parent_run_id"] == parent["id"]
        assert child["trigger_question_id"] == follow_up["id"]
        assert "Focus on West region." in child["question"]

        runs = client.get(f"/api/sessions/{session['id']}/runs").json()
        refreshed_parent = next(run for run in runs if run["id"] == parent["id"])
        assert refreshed_parent["status"] == "completed"
        assert follow_up["id"] not in [item["id"] for item in refreshed_parent["follow_up_questions"]]
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{child['id']}/workspace").json()
        context = next(item for item in workspace if item["path"] == "/follow-up/context.json")
        assert context["content"]["parent_run_id"] == parent["id"]
        assert context["content"]["attached_file_ids"] == [uploaded["id"]]


def test_follow_up_child_run_uses_parent_context_and_selected_sources_only(monkeypatch, tmp_path):
    captured: dict[str, dict] = {}

    async def fake_plan_task(self, **kwargs):
        if kwargs["question"].startswith("Follow up on the completed chart insight."):
            captured["child_plan"] = kwargs
            return {
                "intent": "ask",
                "deliverable": "answer",
                "language": "en",
                "required_outputs": ["answer"],
                "success_criteria": ["answer the selected follow-up"],
                "needs_user_question": False,
            }
        return {
            "intent": "create",
            "deliverable": "answer",
            "language": "en",
            "required_outputs": ["chart"],
            "success_criteria": ["create the best chart"],
            "needs_user_question": False,
        }

    async def fake_chat(self, **kwargs):
        if kwargs["question"].startswith("Follow up on the completed chart insight."):
            captured["child_writer"] = kwargs
        return ChatResult(answer="Child follow-up answer.", cited_source_ids=[1])

    monkeypatch.setattr(OpenRouterClient, "plan_task", fake_plan_task)
    monkeypatch.setattr(OpenRouterClient, "chat", fake_chat)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Selected follow-up context"}).json()
        body = Path("test_documents/correlated_business/regional_demand_forecast.csv").read_bytes()
        selected_file = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("regional_demand_forecast.csv", body, "text/csv")},
        ).json()[0]
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("unselected.txt", b"This should not enter the child prompt.", "text/plain")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "best chart for this file"})
        parent = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        follow_up = parent["follow_up_questions"][0]
        client.post(
            f"/api/sessions/{session['id']}/runs/{parent['id']}/questions/{follow_up['id']}/answer",
            json={
                "selected_option": "inspect_mix",
                "free_text": "Focus on West region.",
                "attached_file_ids": [selected_file["id"]],
            },
        )

        child_plan_context = captured["child_plan"]["prompt_context"]["follow_up_context"]
        assert child_plan_context["parent_run_id"] == parent["id"]
        assert child_plan_context["trigger_question_id"] == follow_up["id"]
        assert child_plan_context["answer"]["free_text"] == "Focus on West region."
        assert child_plan_context["parent_chart_spec"]["insight_narrative"]["source_columns"] == ["forecast_month", "forecast_units"]

        child_writer = captured["child_writer"]
        assert {source["file_name"] for source in child_writer["sources"]} == {"regional_demand_forecast.csv"}
        writer_context = child_writer["prompt_context"]
        assert writer_context["follow_up_context"]["attached_file_ids"] == [selected_file["id"]]
        assert {source["file_name"] for source in writer_context["selected_source_refs"]} == {"regional_demand_forecast.csv"}


def test_multi_select_artifact_choice_creates_child_from_server_option_metadata(monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    async def noop_execute(run_id: str):
        captured["child_run_id"] = run_id

    monkeypatch.setattr("backend.app.main.execute_agent_run", noop_execute)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Produce choices"}).json()
        parent = create_agent_run(session["id"], "what charts and docs can you make with this?")
        server_options = [
            {
                "id": "summary_grounded",
                "label": "Grounded summary",
                "description": "Summarize the source-backed findings.",
                "artifact_kind": "summary_panel",
                "produce_payload": {"instruction": "Create a grounded summary from the current session sources."},
            },
            {
                "id": "draft_plan",
                "label": "Execution draft",
                "description": "Draft an implementation document.",
                "artifact_kind": "file_draft",
                "produce_payload": {"instruction": "Create a grounded execution draft from the current session sources."},
            },
        ]
        question = create_run_question(
            parent.id,
            action_kind="write",
            kind="artifact_choice",
            question="Select one or more artifacts to produce.",
            options=[{key: option[key] for key in ("id", "label", "description")} for option in server_options],
            blocking=False,
            phase="artifact_choice",
            card={
                "title": "Available Charts And Docs",
                "prompt": "Select one or more artifacts to produce.",
                "group": "business",
                "options": server_options,
                "allow_free_text": False,
                "allow_file_reference": False,
                "allow_multi_select": True,
                "submit_label": "Produce selected",
            },
        )

        response = client.post(
            f"/api/sessions/{session['id']}/runs/{parent.id}/questions/{question.id}/answer",
            json={
                "free_text": "IGNORE THIS CLIENT PROMPT",
                "answer": {
                    "selected_options": ["summary_grounded", "draft_plan"],
                    "produce_payload": {"instruction": "MALICIOUS CLIENT PROMPT"},
                },
            },
        )

        assert response.status_code == 200
        child = response.json()
        assert child["parent_run_id"] == parent.id
        assert child["trigger_question_id"] == question.id
        assert "Grounded summary" in child["question"]
        assert "Execution draft" in child["question"]
        assert "MALICIOUS" not in child["question"]
        assert "IGNORE THIS CLIENT PROMPT" not in child["question"]

        workspace = client.get(f"/api/sessions/{session['id']}/runs/{child['id']}/workspace").json()
        context = next(item for item in workspace if item["path"] == "/follow-up/context.json")
        assert context["content"]["answer"]["selected_options"] == ["summary_grounded", "draft_plan"]
        assert [item["id"] for item in context["content"]["selected_artifact_options"]] == ["summary_grounded", "draft_plan"]
        assert context["content"]["selected_artifact_options"][0]["produce_payload"]["instruction"].startswith("Create a grounded summary")
        assert "MALICIOUS" not in json.dumps(context["content"])


def test_multi_select_artifact_choice_rejects_invalid_selected_ids(monkeypatch, tmp_path):
    async def noop_execute(run_id: str):
        return None

    monkeypatch.setattr("backend.app.main.execute_agent_run", noop_execute)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Tampered choices"}).json()
        parent = create_agent_run(session["id"], "what charts and docs can you make with this?")
        question = create_run_question(
            parent.id,
            action_kind="write",
            kind="artifact_choice",
            question="Select one or more artifacts to produce.",
            options=[{"id": "summary_grounded", "label": "Grounded summary", "description": ""}],
            blocking=False,
            phase="artifact_choice",
            card={
                "title": "Available Charts And Docs",
                "prompt": "Select one or more artifacts to produce.",
                "group": "business",
                "options": [
                    {
                        "id": "summary_grounded",
                        "label": "Grounded summary",
                        "description": "",
                        "artifact_kind": "summary_panel",
                        "produce_payload": {"instruction": "Create a grounded summary."},
                    }
                ],
                "allow_free_text": False,
                "allow_file_reference": False,
                "allow_multi_select": True,
                "submit_label": "Produce selected",
            },
        )

        response = client.post(
            f"/api/sessions/{session['id']}/runs/{parent.id}/questions/{question.id}/answer",
            json={"answer": {"selected_options": ["summary_grounded", "tampered"]}},
        )

        assert response.status_code == 400
        assert "Invalid artifact option" in response.json()["detail"]


def test_follow_up_answer_rejects_file_outside_session(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Forecast follow-up"}).json()
        other = client.post("/api/sessions", json={"title": "Other"}).json()
        body = Path("test_documents/correlated_business/regional_demand_forecast.csv").read_bytes()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("regional_demand_forecast.csv", body, "text/csv")},
        )
        other_file = client.post(
            f"/api/sessions/{other['id']}/files",
            files={"uploads": ("other.txt", b"Other session file.", "text/plain")},
        ).json()[0]

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "best chart for this file"})
        parent = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        follow_up = parent["follow_up_questions"][0]

        response = client.post(
            f"/api/sessions/{session['id']}/runs/{parent['id']}/questions/{follow_up['id']}/answer",
            json={"selected_option": "inspect_mix", "attached_file_ids": [other_file["id"]]},
        )

        assert response.status_code == 400
        assert "not ready in this session" in response.json()["detail"]


def test_follow_up_answer_requires_reference_when_card_allows_files(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Missing follow-up reference"}).json()
        body = Path("test_documents/correlated_business/regional_demand_forecast.csv").read_bytes()
        client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("regional_demand_forecast.csv", body, "text/csv")},
        )

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "best chart for this file"})
        parent = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        follow_up = parent["follow_up_questions"][0]

        response = client.post(
            f"/api/sessions/{session['id']}/runs/{parent['id']}/questions/{follow_up['id']}/answer",
            json={"selected_option": "inspect_mix"},
        )

        assert response.status_code == 400
        assert "ready reference file" in response.json()["detail"]


def test_follow_up_answer_rejects_replay(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Forecast replay"}).json()
        body = Path("test_documents/correlated_business/regional_demand_forecast.csv").read_bytes()
        uploaded = client.post(
            f"/api/sessions/{session['id']}/files",
            files={"uploads": ("regional_demand_forecast.csv", body, "text/csv")},
        ).json()[0]

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "best chart for this file"})
        parent = client.get(f"/api/sessions/{session['id']}/runs").json()[0]
        follow_up = parent["follow_up_questions"][0]

        first = client.post(
            f"/api/sessions/{session['id']}/runs/{parent['id']}/questions/{follow_up['id']}/answer",
            json={"selected_option": "inspect_mix", "attached_file_ids": [uploaded["id"]]},
        )
        second = client.post(
            f"/api/sessions/{session['id']}/runs/{parent['id']}/questions/{follow_up['id']}/answer",
            json={"selected_option": "inspect_mix", "attached_file_ids": [uploaded["id"]]},
        )

        assert first.status_code == 200
        assert second.status_code == 409
        assert "already been answered" in second.json()["detail"]

def test_artifact_pdf_export_returns_downloadable_pdf(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "PDF export"}).json()
        created = now()
        with connect() as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                ("msg_pdf_export", session["id"], "assistant", "Draft ready.", created),
            )
            conn.execute(
                """
                INSERT INTO artifacts
                  (id, session_id, message_id, kind, title, caption, display_mode, source_chunk_ids, spec_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "art_pdf_export",
                    session["id"],
                    "msg_pdf_export",
                    "file_draft",
                    "Board Memo",
                    "Grounded draft",
                    "primary",
                    json.dumps([]),
                    json.dumps({"filename": "board-memo.md", "content": "# Board Memo\n\nRevenue rose 12% from enterprise renewals."}),
                    created,
                ),
            )
        response = client.get(f"/api/sessions/{session['id']}/artifacts/art_pdf_export/export?format=pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert 'filename="board-memo.pdf"' in response.headers["content-disposition"]
        assert response.content.startswith(b"%PDF-")
        assert b"Board Memo" in response.content
        assert b"Revenue rose 12%" in response.content
