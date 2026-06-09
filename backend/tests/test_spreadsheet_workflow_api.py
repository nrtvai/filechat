from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.main import app


def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("FILECHAT_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    return TestClient(app)


def workflow_summary(workbook: str, worksheet: str, raw_csv: str) -> str:
    headers = raw_csv.splitlines()[0]
    rows = [line for line in raw_csv.splitlines()[1:] if line.strip()]
    return (
        "# Excel Mode Spreadsheet Summary\n\n"
        f"Workbook: {workbook}\n"
        "Mode: Excel / spreadsheet analysis lane\n\n"
        f"## Worksheet: {worksheet}\n"
        f"Rows: {len(rows)}\n"
        f"Columns: {len(headers.split(','))}\n"
        f"Headers: {headers.replace(',', ', ')}\n\n"
        "## Raw Data (CSV)\n"
        "```csv\n"
        f"{raw_csv.rstrip()}\n"
        "```\n"
    )


def test_workflow_interview_endpoint_returns_questions_for_vague_requests(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/workflows/interview",
            json={"description": "I need help automating my spreadsheet workflow."},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_interview"
    assert payload["ready_to_generate"] is False
    assert payload["required_questions"]
    assert "Which source spreadsheet files" in payload["required_questions"][0]
    assert "html" not in payload
    assert "workflow" not in payload


def test_workflow_generate_endpoint_builds_local_html_for_specified_workflows(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/workflows/generate",
            json={
                "description": "turn my weekly spreadsheet copy/paste/edit reconciliation into a local HTML app",
                "file_texts": [
                    {
                        "file_id": "inventory",
                        "file_name": "inventory.csv",
                        "text": workflow_summary("inventory.csv", "inventory", "sku,qty\nA-1,10\nB-2,5\n"),
                    },
                    {
                        "file_id": "orders",
                        "file_name": "orders.csv",
                        "text": workflow_summary("orders.csv", "orders", "sku,qty\nA-1,12\nC-3,7\n"),
                    },
                ],
                "sources": [],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "generated"
    assert payload["filename"] == "spreadsheet-workflow-automator.html"
    assert payload["content_type"] == "text/html"
    assert payload["html"].startswith("<!doctype html>")
    assert "Spreadsheet Workflow Automator" in payload["html"]
    assert "window.__WORKFLOW__" in payload["html"]
    assert "http://" not in payload["html"]
    assert "https://" not in payload["html"]


def test_workflow_generate_endpoint_refuses_vague_requests_without_html(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/workflows/generate",
            json={"description": "make a spreadsheet automation thing"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_interview"
    assert payload["ready_to_generate"] is False
    assert payload["required_questions"]
    assert "html" not in payload
