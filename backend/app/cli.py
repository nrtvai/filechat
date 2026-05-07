from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings
from .database import connect, init_db
from .ingest import process_file
from .live_artifact_probe import LIVE_FALLBACK_MODELS, LiveArtifactAutonomyProbe, write_live_report
from .main import message_out
from .notion_client import NotionPublishError, publish_artifact
from .notion_export import markdown_for_artifact, notion_import_bundle, slugify_filename
from .retrieval import answer
from .smoke_runner import DEFAULT_MODELS, SmokeRunner, create_correlated_business_extreme_fixtures, create_correlated_business_fixtures, normalize_model_id
from .utils import extension, json_loads, new_id, now, sha256_bytes


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="filechat")
    parser.add_argument("--api-url", help="Target a running FileChat API instead of the local runtime.")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify")
    verify.set_defaults(func=cmd_verify)

    session = sub.add_parser("session")
    session_sub = session.add_subparsers(dest="session_command", required=True)
    session_create = session_sub.add_parser("create")
    session_create.add_argument("--title", default="CLI session")
    session_create.set_defaults(func=cmd_session_create)

    upload = sub.add_parser("upload")
    upload.add_argument("session_id")
    upload.add_argument("paths", nargs="+")
    upload.set_defaults(func=cmd_upload)

    ask = sub.add_parser("ask")
    ask.add_argument("session_id")
    ask.add_argument("prompt")
    ask.add_argument("--auto-answer", action="store_true")
    ask.add_argument("--export-dir")
    ask.set_defaults(func=cmd_ask)

    export = sub.add_parser("export")
    export.add_argument("session_id")
    export.add_argument("artifact_id")
    export.add_argument("--format", choices=["md", "json", "notion"], default="md")
    export.add_argument("--out")
    export.set_defaults(func=cmd_export)

    smoke = sub.add_parser("smoke")
    smoke_sub = smoke.add_subparsers(dest="smoke_command", required=True)
    fixtures = smoke_sub.add_parser("fixtures")
    fixtures.add_argument("--data-dir", default=".filechat-smoke")
    fixtures.add_argument("--variant", choices=["default", "extreme"], default="default")
    fixtures.set_defaults(func=cmd_smoke_fixtures)
    models = smoke_sub.add_parser("models")
    models.add_argument("--models", default=",".join(DEFAULT_MODELS))
    models.add_argument("--data-dir", default=".filechat-openrouter-smoke")
    models.add_argument("--export", default="reports/openrouter-smoke.json")
    models.add_argument("--prompt-timeout", type=float, default=45.0)
    models.set_defaults(func=cmd_smoke_models)
    live = smoke_sub.add_parser("live-artifact-autonomy")
    live.add_argument("--base-url", default="http://127.0.0.1:8000")
    live.add_argument("--web-url", default="http://127.0.0.1:5173")
    live.add_argument("--work-dir", default=".filechat-live-artifact-autonomy")
    live.add_argument("--export", default="")
    live.add_argument("--models", default=",".join(LIVE_FALLBACK_MODELS))
    live.add_argument("--timeout", type=float, default=240.0)
    live.set_defaults(func=cmd_smoke_live_artifact_autonomy)

    notion = sub.add_parser("notion")
    notion_sub = notion.add_subparsers(dest="notion_command", required=True)
    publish = notion_sub.add_parser("publish")
    publish.add_argument("--fixture", default="correlated_business")
    publish.add_argument("--title", default="FileChat Smoke Report")
    publish.set_defaults(func=cmd_notion_publish)
    return parser


def cmd_verify(args: argparse.Namespace) -> int:
    if args.api_url:
        payload = httpx.get(f"{args.api_url.rstrip('/')}/api/health", timeout=10).json()
        print(json.dumps(payload, indent=2))
        return 0
    init_db()
    settings = get_settings()
    payload = {
        "status": "ok",
        "mode": "local",
        "data_dir": str(settings.resolved_data_dir),
        "openrouter_key_configured": bool(settings.openrouter_api_key),
        "notion_configured": bool(settings.notion_api_key and settings.notion_parent_page_id),
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_session_create(args: argparse.Namespace) -> int:
    if args.api_url:
        response = httpx.post(f"{args.api_url.rstrip('/')}/api/sessions", json={"title": args.title}, timeout=30)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))
        return 0
    init_db()
    session_id = new_id("ses")
    created = now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, title, organization_id, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, args.title, "org_single", "usr_single", created, created),
        )
    print(json.dumps({"id": session_id, "title": args.title, "created_at": created}, indent=2))
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    if args.api_url:
        files = [("uploads", (Path(path).name, Path(path).read_bytes())) for path in args.paths]
        response = httpx.post(f"{args.api_url.rstrip('/')}/api/sessions/{args.session_id}/files", files=files, timeout=120)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))
        return 0
    uploaded = asyncio.run(upload_paths(args.session_id, [Path(path) for path in args.paths]))
    print(json.dumps(uploaded, indent=2))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    if args.api_url:
        response = httpx.post(
            f"{args.api_url.rstrip('/')}/api/sessions/{args.session_id}/messages",
            json={"content": args.prompt},
            timeout=240,
        )
        response.raise_for_status()
        payload = response.json()
    else:
        init_db()
        message_id = asyncio.run(answer(args.session_id, args.prompt))
        with connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        payload = message_out(row).model_dump()
    if args.export_dir:
        payload["exported_files"] = export_message_artifacts(payload, Path(args.export_dir))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    if args.api_url:
        response = httpx.get(
            f"{args.api_url.rstrip('/')}/api/sessions/{args.session_id}/artifacts/{args.artifact_id}/export",
            params={"format": args.format},
            timeout=60,
        )
        response.raise_for_status()
        content = response.text
    else:
        content, _ = local_export_artifact(args.session_id, args.artifact_id, args.format)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
    else:
        print(content)
    return 0


def cmd_smoke_fixtures(args: argparse.Namespace) -> int:
    fixture = "correlated_business_extreme" if args.variant == "extreme" else "correlated_business"
    creator = create_correlated_business_extreme_fixtures if args.variant == "extreme" else create_correlated_business_fixtures
    paths = creator(Path(args.data_dir) / fixture)
    print(json.dumps({"fixture": fixture, "variant": args.variant, "paths": [str(path) for path in paths]}, indent=2))
    return 0


def cmd_smoke_models(args: argparse.Namespace) -> int:
    models = [normalize_model_id(model) for model in args.models.split(",") if model.strip()]
    report = asyncio.run(SmokeRunner(data_dir=Path(args.data_dir), models=models, prompt_timeout=args.prompt_timeout).run())
    out = Path(args.export)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"export": str(out), "status": report["status"], "runs": len(report["runs"])}, indent=2))
    return 0


def cmd_smoke_live_artifact_autonomy(args: argparse.Namespace) -> int:
    models = [normalize_model_id(model) for model in args.models.split(",") if model.strip()]
    report = LiveArtifactAutonomyProbe(
        base_url=args.base_url,
        web_url=args.web_url,
        work_dir=Path(args.work_dir),
        timeout_seconds=args.timeout,
        fallback_models=models,
    ).run()
    out = write_live_report(report, Path(args.export) if args.export else None)
    print(json.dumps({"export": str(out), "status": report["status"]}, indent=2))
    return 0 if report["status"] == "passed" else 1


def cmd_notion_publish(args: argparse.Namespace) -> int:
    report = asyncio.run(SmokeRunner(data_dir=Path(".filechat-notion-smoke"), models=["local-notion"]).publish_fixture(args.fixture, args.title))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


async def upload_paths(session_id: str, paths: list[Path]) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        if not conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone():
            raise RuntimeError(f"Session not found: {session_id}")
    uploaded: list[dict[str, Any]] = []
    for path in paths:
        body = path.read_bytes()
        digest = sha256_bytes(body)
        ext = extension(path.name)
        stored_path = get_settings().resolved_data_dir / "uploads" / f"{digest}.{ext}"
        if not stored_path.exists():
            shutil.copyfile(path, stored_path)
        file_id = new_id("fil")
        created = now()
        with connect() as conn:
            existing = conn.execute("SELECT * FROM files WHERE hash = ? AND organization_id = ?", (digest, "org_single")).fetchone()
            if existing:
                file_id = existing["id"]
            else:
                conn.execute(
                    """
                    INSERT INTO files
                    (id, hash, organization_id, created_by, name, type, size, path, status, progress, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (file_id, digest, "org_single", "usr_single", path.name, ext.upper(), len(body), str(stored_path), "queued", 0, created, created),
                )
            conn.execute("INSERT OR IGNORE INTO session_files (session_id, file_id, attached_at) VALUES (?, ?, ?)", (session_id, file_id, now()))
            should_process = not existing or existing["status"] in {"failed", "queued"}
            if should_process:
                conn.execute("UPDATE files SET status = ?, progress = ?, error = NULL, updated_at = ? WHERE id = ?", ("queued", 0, now(), file_id))
        if should_process:
            await process_file(file_id, session_id)
        with connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        uploaded.append(dict(row))
    return uploaded


def local_export_artifact(session_id: str, artifact_id: str, format: str) -> tuple[str, str]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id = ? AND session_id = ?", (artifact_id, session_id)).fetchone()
    if not row:
        raise RuntimeError(f"Artifact not found: {artifact_id}")
    spec = json_loads(row["spec_json"], {})
    row_dict = dict(row)
    row_dict["source_chunk_ids"] = json_loads(row["source_chunk_ids"], [])
    if format == "json":
        return json.dumps(spec, ensure_ascii=False, indent=2), slugify_filename(str(spec.get("filename") or row["title"]), ".json")
    if format == "notion":
        return json.dumps(notion_import_bundle(row_dict, spec), ensure_ascii=False, indent=2), slugify_filename(str(row["title"]), ".json")
    return markdown_for_artifact(row_dict, spec)


def export_message_artifacts(message: dict[str, Any], export_dir: Path) -> list[str]:
    export_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for artifact in message.get("artifacts", []):
        artifact_id = str(artifact["id"])
        session_id = str(artifact["session_id"])
        for format in ("md", "json", "notion"):
            content, filename = local_export_artifact(session_id, artifact_id, format)
            out = export_dir / f"{artifact_id}-{filename}"
            out.write_text(content, encoding="utf-8")
            paths.append(str(out))
    return paths


async def publish_latest_file_draft(session_id: str, *, title: str) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM artifacts
            WHERE session_id = ? AND kind = 'file_draft'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if not row:
        raise RuntimeError("No file_draft artifact is available to publish.")
    spec = json_loads(row["spec_json"], {})
    try:
        return await publish_artifact(dict(row), spec, title=title)
    except NotionPublishError:
        raise
