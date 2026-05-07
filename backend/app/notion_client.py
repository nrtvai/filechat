from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import httpx

from .config import get_settings
from .notion_export import notion_import_bundle

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionPublishError(RuntimeError):
    pass


@dataclass
class NotionPublishResult:
    page_id: str
    page_url: str | None = None
    database_id: str | None = None
    row_page_ids: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "page_url": self.page_url,
            "database_id": self.database_id,
            "row_page_ids": self.row_page_ids or [],
        }


class NotionClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.notion_api_key
        self.parent_page_id = normalize_notion_id(settings.notion_parent_page_id or "")
        if not self.api_key or not self.parent_page_id:
            raise NotionPublishError("NOTION_API_KEY and NOTION_PARENT_PAGE_ID are required for live publishing.")

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }

    async def publish_bundle(self, bundle: dict[str, Any], *, title: str | None = None) -> NotionPublishResult:
        page_title = title or str(bundle.get("metadata", {}).get("title") or "FileChat Report")
        blocks = markdown_to_blocks(str(bundle.get("markdown") or ""))
        async with httpx.AsyncClient(timeout=60) as client:
            page_payload = {
                "parent": {"type": "page_id", "page_id": self.parent_page_id},
                "properties": {"title": {"title": [{"type": "text", "text": {"content": page_title[:2000]}}]}},
                "children": blocks[:100],
            }
            page_response = await client.post(f"{NOTION_API_URL}/pages", headers=self.headers(), json=page_payload)
            if page_response.status_code >= 400:
                raise NotionPublishError(f"Notion page create failed: {page_response.status_code} {safe_error(page_response)}")
            page = page_response.json()
            database_id = None
            row_page_ids: list[str] = []
            table = bundle.get("datatable")
            if isinstance(table, dict) and table.get("columns") and table.get("rows"):
                database_id, row_page_ids = await self._publish_table(client, page["id"], page_title, table)
        return NotionPublishResult(
            page_id=str(page["id"]),
            page_url=page.get("url") if isinstance(page, dict) else None,
            database_id=database_id,
            row_page_ids=row_page_ids,
        )

    async def _publish_table(
        self,
        client: httpx.AsyncClient,
        page_id: str,
        title: str,
        table: dict[str, Any],
    ) -> tuple[str, list[str]]:
        columns = [str(column) for column in table.get("columns", [])]
        rows = [[str(cell) for cell in row] for row in table.get("rows", []) if isinstance(row, list)]
        if not columns or not rows:
            return "", []
        properties: dict[str, Any] = {columns[0]: {"title": {}}}
        for column in columns[1:]:
            properties[column] = {"rich_text": {}}
        payload = {
            "parent": {"type": "page_id", "page_id": page_id},
            "title": [{"type": "text", "text": {"content": f"{title} Data"[:2000]}}],
            "properties": properties,
        }
        response = await client.post(f"{NOTION_API_URL}/databases", headers=self.headers(), json=payload)
        if response.status_code >= 400:
            raise NotionPublishError(f"Notion database create failed: {response.status_code} {safe_error(response)}")
        database = response.json()
        database_id = str(database["id"])
        row_page_ids: list[str] = []
        for row in rows[:100]:
            page_properties = {
                columns[0]: {"title": [{"type": "text", "text": {"content": row[0][:2000] if row else ""}}]},
            }
            for index, column in enumerate(columns[1:], start=1):
                page_properties[column] = {
                    "rich_text": [{"type": "text", "text": {"content": (row[index] if index < len(row) else "")[:2000]}}],
                }
            response = await client.post(
                f"{NOTION_API_URL}/pages",
                headers=self.headers(),
                json={"parent": {"type": "database_id", "database_id": database_id}, "properties": page_properties},
            )
            if response.status_code >= 400:
                raise NotionPublishError(f"Notion table row create failed: {response.status_code} {safe_error(response)}")
            row_page_ids.append(str(response.json()["id"]))
        return database_id, row_page_ids


async def publish_artifact(row: dict[str, Any], spec: dict[str, Any], *, title: str | None = None) -> dict[str, Any]:
    bundle = notion_import_bundle(row, spec)
    return (await NotionClient().publish_bundle(bundle, title=title)).as_dict()


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            blocks.append(_text_block("heading_1", line[2:]))
        elif line.startswith("## "):
            blocks.append(_text_block("heading_2", line[3:]))
        elif line.startswith("### "):
            blocks.append(_text_block("heading_3", line[4:]))
        elif line.startswith("- "):
            blocks.append(_text_block("bulleted_list_item", line[2:]))
        elif line.startswith("|"):
            blocks.append(_text_block("paragraph", line))
        else:
            blocks.append(_text_block("paragraph", line))
    return blocks or [_text_block("paragraph", "Generated by FileChat.")]


def _text_block(kind: str, text: str) -> dict[str, Any]:
    key = "rich_text"
    return {
        "object": "block",
        "type": kind,
        kind: {key: [{"type": "text", "text": {"content": text[:2000]}}]},
    }


def safe_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        return str(message or payload)[:500]
    except Exception:
        return response.text[:500]


def normalize_notion_id(value: str) -> str:
    cleaned = value.strip()
    matches = re.findall(r"[0-9a-fA-F]{32}", cleaned)
    if matches:
        raw = matches[-1]
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}".lower()
    uuid_match = re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", cleaned)
    return uuid_match.group(0).lower() if uuid_match else cleaned
