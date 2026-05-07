import asyncio
import json

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.artifact_engine import profile_sources
from backend.app.main import app
from backend.app.openrouter import ChatResult, OpenRouterArtifactPlanError, OpenRouterClient, OpenRouterResponseError, _chat_result_from_artifact_payload
from backend.tests.test_api import make_client


NEUTRAL_CSV = b"Stage,Amount,Owner\nIntake,10,Ada\nReview,15,Byron\nReview,5,Ada\nDone,20,Cy\n"


CSV_SHAPES = [
    (
        "sales_orders.csv",
        "OrderId,Product,Units,Amount,Date\nA-1,Widget,3,120,2026-01-02\nA-2,Gadget,2,80,2026-01-03\n",
        ["OrderId", "Product", "Units", "Amount", "Date"],
    ),
    (
        "inventory.csv",
        "Item,Location,OnHand,Threshold\nWidget,North,12,10\nGadget,South,4,8\n",
        ["Item", "Location", "OnHand", "Threshold"],
    ),
    (
        "demand_forecast.csv",
        "Month,Region,ForecastUnits\n2026-01,North,240\n2026-02,South,180\n",
        ["Month", "Region", "ForecastUnits"],
    ),
    (
        "milestones.csv",
        "Phase,TargetDate,Owner,Status\nKickoff,2026-01-10,Ada,Done\nReview,2026-02-15,Byron,Planned\n",
        ["Phase", "TargetDate", "Owner", "Status"],
    ),
    (
        "neutral_table.csv",
        NEUTRAL_CSV.decode(),
        ["Stage", "Amount", "Owner"],
    ),
]


def upload_neutral_csv(client, session_id: str, name: str = "neutral_table.csv"):
    return client.post(
        f"/api/sessions/{session_id}/files",
        files={"uploads": (name, NEUTRAL_CSV, "text/csv")},
    )


def latest_run(client, session_id: str) -> dict:
    return client.get(f"/api/sessions/{session_id}/runs").json()[0]


def latest_message(client, session_id: str) -> dict:
    return client.get(f"/api/sessions/{session_id}/messages").json()[-1]


def test_source_profile_handles_unrelated_csv_shapes_generically():
    file_texts = [
        {"file_id": f"file_{index}", "file_name": name, "text": text}
        for index, (name, text, _columns) in enumerate(CSV_SHAPES, start=1)
    ]
    sources = [
        {
            "source_id": index,
            "file_id": f"file_{index}",
            "file_name": name,
            "location": "chunk 1",
            "chunk_id": f"chunk_{index}",
            "content": text,
            "excerpt": text[:120],
        }
        for index, (name, text, _columns) in enumerate(CSV_SHAPES, start=1)
    ]

    profile = profile_sources(file_texts, sources)

    assert profile["diagnostics"]["table_count"] == len(CSV_SHAPES)
    assert profile["available_operations"] == [
        "group",
        "sum",
        "count",
        "average",
        "min",
        "max",
        "top_categories",
        "time_bucket",
    ]
    for table, (_name, _text, expected_columns) in zip(profile["tables"], CSV_SHAPES):
        expected_rows = 4 if expected_columns == ["Stage", "Amount", "Owner"] else 2
        assert table["row_count"] == expected_rows
        assert [column["name"] for column in table["columns"]] == expected_columns
        assert table["source_ids"]
        assert table["source_chunk_ids"]


def test_openrouter_artifact_planner_accepts_nested_plan(monkeypatch):
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    get_settings.cache_clear()

    async def fake_json_completion(self, **kwargs):
        return {
            "artifact_plan": {
                "mode": "discovery",
                "options": [
                    {
                        "option_id": "stage_counts",
                        "type": "chart",
                        "visualization_type": "bar",
                        "label": "Stage counts",
                        "reason": "Count records by Stage.",
                        "columns": ["Stage"],
                        "source_ids": ["1"],
                    }
                ],
                "reasoning": "The source profile exposes a categorical column.",
                "source_ids": ["1"],
                "limitations": ["Grouped counts only."],
                "success_criteria": ["Decision cards only."],
            }
        }

    async def fake_candidates(self, model):
        return [model]

    monkeypatch.setattr(OpenRouterClient, "_artifact_planner_candidates", fake_candidates)
    monkeypatch.setattr(OpenRouterClient, "_json_completion", fake_json_completion)
    plan = asyncio.run(
        OpenRouterClient().plan_artifacts(
            model="deepseek/deepseek-v4-pro",
            question="what charts can we make?",
            task_contract={"required_outputs": ["decision_cards"]},
            source_profile={"summary": "profile", "sources": [{"source_id": 1}]},
            prompt_context={"packet_kind": "writer"},
            selected_options=[],
            discovery_only=True,
        )
    )

    assert plan["artifacts"][0]["id"] == "stage_counts"
    assert plan["artifacts"][0]["artifact_kind"] == "chart"
    assert plan["artifacts"][0]["chart_type"] == "bar"
    assert plan["required_citations"] == [1]
    get_settings.cache_clear()


def test_openrouter_artifact_planner_uses_strict_json_schema(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_candidates(self, model):
        return [model]

    async def fake_json_completion(self, **kwargs):
        captured.update(kwargs)
        return {
            "mode": "discovery",
            "artifacts": [
                {
                    "id": "stage_counts",
                    "artifact_kind": "chart",
                    "chart_type": "bar",
                    "title": "Stage counts",
                    "description": "Count records by Stage.",
                    "source_columns": ["Stage"],
                    "required_source_ids": [1],
                    "caveats": [],
                    "acceptance_criteria": ["Decision card only."],
                }
            ],
            "rationale": "Stage is categorical.",
            "required_citations": [1],
            "caveats": [],
            "acceptance_criteria": ["Return decision cards."],
        }

    monkeypatch.setattr(OpenRouterClient, "_artifact_planner_candidates", fake_candidates)
    monkeypatch.setattr(OpenRouterClient, "_json_completion", fake_json_completion)

    plan = asyncio.run(
        OpenRouterClient().plan_artifacts(
            model="deepseek/deepseek-v4-pro",
            question="what charts can we make?",
            task_contract={"required_outputs": ["decision_cards"]},
            source_profile={"summary": "profile", "sources": [{"source_id": 1}]},
            prompt_context={"packet_kind": "writer"},
            selected_options=[],
            discovery_only=True,
        )
    )

    response_format = captured["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert captured["require_parameters"] is True
    assert "schema" not in json.loads(str(captured["user"]))
    assert plan["_effective_planner_model"] == "deepseek/deepseek-v4-pro"


def test_openrouter_artifact_planner_recovers_schema_only_with_fallback(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_candidates(self, model):
        return ["deepseek/deepseek-v4-pro", "openai/gpt-4o-mini"]

    async def fake_json_completion(self, **kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "deepseek/deepseek-v4-pro":
            return {"schema": {"artifacts": []}}
        return {
            "mode": "discovery",
            "artifacts": [
                {
                    "id": "stage_counts",
                    "artifact_kind": "chart",
                    "chart_type": "bar",
                    "title": "Stage counts",
                    "description": "Count records by Stage.",
                    "source_columns": ["Stage"],
                    "required_source_ids": [1],
                    "caveats": [],
                    "acceptance_criteria": ["Decision card only."],
                }
            ],
            "rationale": "Fallback planner produced a source-grounded option.",
            "required_citations": [1],
            "caveats": [],
            "acceptance_criteria": ["Return decision cards."],
        }

    monkeypatch.setattr(OpenRouterClient, "_artifact_planner_candidates", fake_candidates)
    monkeypatch.setattr(OpenRouterClient, "_json_completion", fake_json_completion)

    plan = asyncio.run(
        OpenRouterClient().plan_artifacts(
            model="deepseek/deepseek-v4-pro",
            question="what charts can we make?",
            task_contract={"required_outputs": ["decision_cards"]},
            source_profile={"summary": "profile", "sources": [{"source_id": 1}]},
            prompt_context={"packet_kind": "writer"},
            selected_options=[],
            discovery_only=True,
        )
    )

    assert [call["model"] for call in calls] == [
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-pro",
        "openai/gpt-4o-mini",
    ]
    assert plan["_effective_planner_model"] == "openai/gpt-4o-mini"
    assert [attempt["response_class"] for attempt in plan["_planner_attempts"]] == ["schema_only", "schema_only", "artifact_plan"]


def test_openrouter_artifact_builder_normalizes_common_artifact_aliases():
    result = _chat_result_from_artifact_payload(
        {
            "answer": "Built charts.",
            "cited_source_ids": [1],
            "artifacts": [
                {
                    "artifact_kind": "chart",
                    "title": "Stage counts",
                    "description": "Count records by Stage.",
                    "required_source_ids": [1],
                    "chart_type": "bar",
                    "values": [{"label": "Review", "value": 2, "source_id": 1}],
                }
            ],
        },
        model="openai/gpt-4o-mini",
    )

    assert result.artifacts[0]["kind"] == "chart"
    assert result.artifacts[0]["caption"] == "Count records by Stage."
    assert result.artifacts[0]["source_ids"] == [1]


def test_artifact_planner_failure_persists_assistant_message_and_diagnostics(monkeypatch, tmp_path):
    async def failing_plan_artifacts(self, **kwargs):
        raise OpenRouterArtifactPlanError(
            "Artifact planner could not produce a valid plan after live model recovery attempts.",
            attempts=[
                {
                    "attempt": 1,
                    "model": "deepseek/deepseek-v4-pro",
                    "tactic": "selected_strict",
                    "status": "failed",
                    "response_format": "json_schema",
                    "response_class": "schema_only",
                    "response_keys": ["schema"],
                    "error": "Selected artifact planner returned no artifact plan.",
                }
            ],
        )

    monkeypatch.setattr(OpenRouterClient, "plan_artifacts", failing_plan_artifacts, raising=False)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Planner failure"}).json()
        upload_neutral_csv(client, session["id"])

        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"content": "what charts can we make from this file?"},
        )

        assert response.status_code == 200
        run = latest_run(client, session["id"])
        assert run["status"] == "failed"
        assert run["assistant_message_id"]
        assert not any(action["status"] == "running" for action in run["actions"])

        message = latest_message(client, session["id"])
        assert message["role"] == "assistant"
        assert message["artifacts"] == []
        assert "model provider did not return a usable artifact plan response" in message["content"]

        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        paths = {item["path"] for item in workspace}
        assert "/analysis/source-profile.json" in paths
        assert "/review/artifact-plan-failure.json" in paths
        assert "/review/artifact-planner-attempts.json" in paths


def test_ai_first_discovery_returns_only_decision_cards(monkeypatch, tmp_path):
    async def fake_plan_artifacts(self, **kwargs):
        profile = kwargs["source_profile"]
        assert profile["tables"][0]["row_count"] == 4
        assert [column["name"] for column in profile["tables"][0]["columns"]] == ["Stage", "Amount", "Owner"]
        return {
            "mode": "discovery",
            "artifacts": [
                {
                    "id": "stage_counts",
                    "artifact_kind": "chart",
                    "chart_type": "bar",
                    "title": "Stage counts",
                    "description": "Count records by Stage.",
                    "required_source_ids": [1],
                    "caveats": ["Uses grouped counts, not raw rows."],
                    "acceptance_criteria": ["Decision card only."],
                },
                {
                    "id": "amount_by_owner",
                    "artifact_kind": "chart",
                    "chart_type": "bar",
                    "title": "Amount by Owner",
                    "description": "Sum Amount by Owner.",
                    "required_source_ids": [1],
                    "caveats": ["Uses grouped sums, not raw rows."],
                    "acceptance_criteria": ["Decision card only."],
                },
            ],
            "rationale": "The table has one categorical column and one numeric column.",
            "required_citations": [1],
            "caveats": ["Source profile includes samples only."],
            "acceptance_criteria": ["Return decision_cards only."],
        }

    async def fail_build_artifacts(self, **kwargs):
        raise AssertionError("Discovery should not build production artifacts.")

    monkeypatch.setattr(OpenRouterClient, "plan_artifacts", fake_plan_artifacts, raising=False)
    monkeypatch.setattr(OpenRouterClient, "build_artifacts", fail_build_artifacts, raising=False)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Discovery"}).json()
        upload_neutral_csv(client, session["id"])

        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"content": "what charts can we make from this file?"},
        )

        assert response.status_code == 200
        run = latest_run(client, session["id"])
        assert run["status"] == "completed"
        assert run["task_contract"]["required_outputs"] == ["decision_cards"]
        assert run["task_contract"]["executable_contract"]["required_outputs"] == ["decision_cards"]
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        assert {"/analysis/source-profile.json", "/plan/artifact-plan.json", "/build/artifact-build.json"} <= {
            item["path"] for item in workspace
        }
        artifact_check = next(item for item in workspace if item["path"] == "/review/artifact-check.json")
        assert not any("Missing requested artifact: chart" in finding for finding in artifact_check["content"]["findings"])

        message = latest_message(client, session["id"])
        assert [artifact["kind"] for artifact in message["artifacts"]] == ["decision_cards"]
        spec = message["artifacts"][0]["spec"]
        serialized = json.dumps(spec)
        assert "AI adoption" not in serialized
        assert "groupware" not in serialized.lower()
        assert "sales orders" not in serialized.lower()
        assert "Stage,Amount,Owner" not in serialized
        assert [option["id"] for option in spec["decision_options"]] == ["stage_counts", "amount_by_owner"]


def test_selected_decision_cards_create_exact_artifacts_and_ignore_client_payload(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_plan_artifacts(self, **kwargs):
        selected = kwargs.get("selected_options") or []
        if selected:
            raise AssertionError("Selected artifact runs should reuse server-owned discovery options as the plan.")
        return {
            "mode": "discovery",
            "artifacts": [
                {
                    "id": "stage_counts",
                    "artifact_kind": "chart",
                    "chart_type": "bar",
                    "title": "Stage counts",
                    "description": "Count records by Stage.",
                    "required_source_ids": [1],
                    "caveats": [],
                    "acceptance_criteria": [],
                },
                {
                    "id": "owner_table",
                    "artifact_kind": "table",
                    "title": "Owner table",
                    "description": "Preview grouped owner records.",
                    "required_source_ids": [1],
                    "caveats": [],
                    "acceptance_criteria": [],
                },
            ],
            "rationale": "Discovery options.",
            "required_citations": [1],
            "caveats": [],
            "acceptance_criteria": ["Decision cards only."],
        }

    async def fake_build_artifacts(self, **kwargs):
        plan = kwargs["artifact_plan"]
        captured["selected_plan_ids"] = [item["id"] for item in plan["artifacts"]]
        return {
            "answer": "Created the selected chart and table artifacts from cited source 1.",
            "cited_source_ids": [1],
            "artifacts": [
                {
                    "kind": "chart",
                    "title": "Stage counts",
                    "caption": "Count records by Stage.",
                    "source_ids": [1],
                    "chart_type": "bar",
                    "x_label": "Stage",
                    "y_label": "Count",
                    "values": [
                        {"label": "Review", "value": 2, "source_id": 1},
                        {"label": "Intake", "value": 1, "source_id": 1},
                    ],
                },
                {
                    "kind": "table",
                    "title": "Owner table",
                    "caption": "Grouped owner records.",
                    "source_ids": [1],
                    "columns": ["Owner", "Records"],
                    "rows": [["Ada", "2"], ["Byron", "1"]],
                },
            ][: len(plan["artifacts"])],
            "unresolved_issues": [],
        }

    monkeypatch.setattr(OpenRouterClient, "plan_artifacts", fake_plan_artifacts, raising=False)
    monkeypatch.setattr(OpenRouterClient, "build_artifacts", fake_build_artifacts, raising=False)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Selected"}).json()
        upload_neutral_csv(client, session["id"])
        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "what charts and docs can you make with this?"})
        parent = latest_run(client, session["id"])
        follow_up = parent["follow_up_questions"][0]

        response = client.post(
            f"/api/sessions/{session['id']}/runs/{parent['id']}/questions/{follow_up['id']}/answer",
            json={
                "answer": {
                    "selected_options": ["stage_counts", "owner_table"],
                    "produce_payload": {
                        "artifact": {
                            "kind": "file_draft",
                            "title": "Malicious client artifact",
                            "content": "Do not trust this.",
                        }
                    },
                }
            },
        )

        assert response.status_code == 200
        child = response.json()
        assert child["parent_run_id"] == parent["id"]
        assert captured["selected_plan_ids"] == ["stage_counts", "owner_table"]

        message = latest_message(client, session["id"])
        assert "selected chart and table artifacts" in message["content"]
        assert message["citations"]
        assert [artifact["kind"] for artifact in message["artifacts"]] == ["chart", "table"]
        assert "Malicious client artifact" not in json.dumps(message["artifacts"])


def test_invalid_selected_decision_card_id_returns_400(monkeypatch, tmp_path):
    async def fake_plan_artifacts(self, **kwargs):
        return {
            "mode": "discovery",
            "artifacts": [
                {
                    "id": "stage_counts",
                    "artifact_kind": "chart",
                    "chart_type": "bar",
                    "title": "Stage counts",
                    "description": "Count records by Stage.",
                    "required_source_ids": [1],
                    "caveats": [],
                    "acceptance_criteria": [],
                }
            ],
            "rationale": "Discovery options.",
            "required_citations": [1],
            "caveats": [],
            "acceptance_criteria": ["Decision cards only."],
        }

    monkeypatch.setattr(OpenRouterClient, "plan_artifacts", fake_plan_artifacts, raising=False)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Invalid option"}).json()
        upload_neutral_csv(client, session["id"])
        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "what charts can we make from this file?"})
        parent = latest_run(client, session["id"])
        follow_up = parent["follow_up_questions"][0]

        response = client.post(
            f"/api/sessions/{session['id']}/runs/{parent['id']}/questions/{follow_up['id']}/answer",
            json={"answer": {"selected_options": ["stage_counts", "client_supplied_extra"]}},
        )

        assert response.status_code == 400
        assert "Invalid artifact option" in response.json()["detail"]


def test_red_team_failure_repairs_before_persistence(monkeypatch, tmp_path):
    async def fake_plan_artifacts(self, **kwargs):
        return {
            "mode": "create",
            "artifacts": [
                {
                    "id": "stage_counts",
                    "artifact_kind": "chart",
                    "chart_type": "bar",
                    "title": "Stage counts",
                    "description": "Count records by Stage.",
                    "required_source_ids": [1],
                    "caveats": [],
                    "acceptance_criteria": ["Must pass red-team review."],
                }
            ],
            "rationale": "The request asks for a chart.",
            "required_citations": [1],
            "caveats": [],
            "acceptance_criteria": ["Repair high-severity findings before persistence."],
        }

    async def fake_build_artifacts(self, **kwargs):
        return {
            "answer": "Created an unsupported artifact.",
            "cited_source_ids": [1],
            "artifacts": [
                {
                    "kind": "chart",
                    "title": "Bad chart",
                    "source_ids": [1],
                    "chart_type": "bar",
                    "x_label": "Stage",
                    "y_label": "Count",
                    "values": [{"label": "Review", "value": 2, "source_id": 1}],
                }
            ],
            "unresolved_issues": [],
        }

    async def fake_repair_artifacts(self, **kwargs):
        assert kwargs["repair_attempt"] == 1
        return {
            "answer": "Created the repaired Stage counts chart with source citation 1.",
            "cited_source_ids": [1],
            "artifacts": [
                {
                    "kind": "chart",
                    "title": "Stage counts",
                    "caption": "Count records by Stage.",
                    "source_ids": [1],
                    "chart_type": "bar",
                    "x_label": "Stage",
                    "y_label": "Count",
                    "values": [{"label": "Review", "value": 2, "source_id": 1}],
                }
            ],
            "unresolved_issues": [],
        }

    async def fake_review_phase(self, **kwargs):
        if "unsupported" in kwargs["answer_draft"]:
            return {
                "phase": kwargs["phase"],
                "passed": False,
                "severity": "high",
                "findings": ["Unsupported artifact claim."],
                "required_fixes": ["Repair the artifact before persistence."],
                "suggested_followups": [],
                "confidence": "high",
            }
        return {
            "phase": kwargs["phase"],
            "passed": True,
            "severity": "none",
            "findings": [],
            "required_fixes": [],
            "suggested_followups": [],
            "confidence": "high",
        }

    monkeypatch.setattr(OpenRouterClient, "plan_artifacts", fake_plan_artifacts, raising=False)
    monkeypatch.setattr(OpenRouterClient, "build_artifacts", fake_build_artifacts, raising=False)
    monkeypatch.setattr(OpenRouterClient, "repair_artifacts", fake_repair_artifacts, raising=False)
    monkeypatch.setattr(OpenRouterClient, "review_phase", fake_review_phase)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Repair"}).json()
        upload_neutral_csv(client, session["id"])

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Create a chart from this file"})

        run = latest_run(client, session["id"])
        assert run["status"] == "completed"
        assert run["repair_attempts"][0]["strategy"] == "artifact_engine_repair"
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        assert any(item["path"] == "/review/repair-attempt-1.json" for item in workspace)
        message = latest_message(client, session["id"])
        assert message["artifacts"][0]["title"] == "Stage counts"
        assert "repaired Stage counts" in message["content"]


def test_artifact_engine_fails_after_repair_budget_with_diagnostics(monkeypatch, tmp_path):
    async def fake_plan_artifacts(self, **kwargs):
        return {
            "mode": "create",
            "artifacts": [
                {
                    "id": "stage_counts",
                    "artifact_kind": "chart",
                    "chart_type": "bar",
                    "title": "Stage counts",
                    "description": "Count records by Stage.",
                    "required_source_ids": [1],
                    "caveats": [],
                    "acceptance_criteria": ["Must include valid chart values."],
                }
            ],
            "rationale": "The request asks for a chart.",
            "required_citations": [1],
            "caveats": [],
            "acceptance_criteria": ["Repair before persistence."],
        }

    async def bad_build(self, **kwargs):
        return {
            "answer": "Created a chart.",
            "cited_source_ids": [1],
            "artifacts": [
                {
                    "kind": "chart",
                    "title": "Broken chart",
                    "source_ids": [1],
                    "chart_type": "bar",
                    "values": [{"label": "Review", "value": "not numeric", "source_id": 1}],
                }
            ],
            "unresolved_issues": [],
        }

    monkeypatch.setattr(OpenRouterClient, "plan_artifacts", fake_plan_artifacts, raising=False)
    monkeypatch.setattr(OpenRouterClient, "build_artifacts", bad_build, raising=False)
    monkeypatch.setattr(OpenRouterClient, "repair_artifacts", bad_build, raising=False)

    with make_client(monkeypatch, tmp_path) as client:
        session = client.post("/api/sessions", json={"title": "Repair exhausted"}).json()
        upload_neutral_csv(client, session["id"])

        client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Create a chart from this file"})

        run = latest_run(client, session["id"])
        assert run["status"] == "failed"
        assert "could not generate safe artifacts" in run["error"].lower()
        assert len(run["repair_attempts"]) == 2
        workspace = client.get(f"/api/sessions/{session['id']}/runs/{run['id']}/workspace").json()
        assert any(item["path"] == "/review/repair-attempt-2.json" for item in workspace)


def test_missing_provider_degrades_without_fake_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FILECHAT_ALLOW_FAKE_OPENROUTER", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setattr("backend.app.settings_store._keyring_get", lambda: None)
    get_settings.cache_clear()

    with TestClient(app) as client:
        session = client.post("/api/sessions", json={"title": "No provider artifact"}).json()
        upload_neutral_csv(client, session["id"])

        response = client.post(f"/api/sessions/{session['id']}/runs", json={"content": "Create a chart from this file"})

        assert response.status_code == 200
        run = latest_run(client, session["id"])
        assert run["status"] == "completed"
        assert run["provider_status"]["status"] == "missing"
        message = latest_message(client, session["id"])
        assert message["artifacts"] == []
        assert "artifact generation needs a verified model provider" in message["content"].lower()
        assert "4 row" in message["content"]
        assert "Stage" in message["content"]
