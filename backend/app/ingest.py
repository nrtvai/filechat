from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from openai import OpenAI

from .config import get_settings
from .database import connect
from .openrouter import OPENROUTER_URL, OpenRouterClient, OpenRouterMissingKey, OpenRouterResponseError
from .settings_store import current_app_settings, get_openrouter_key
from .usage import record_usage_event
from .utils import new_id, now, rough_tokens, sha256_text


def split_chunks(text: str, *, target_tokens: int = 820, overlap_tokens: int = 90) -> list[str]:
    blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    if not blocks:
        blocks = [text.strip()] if text.strip() else []
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for block in blocks:
        block_tokens = rough_tokens(block)
        if current and current_tokens + block_tokens > target_tokens:
            chunks.append("\n\n".join(current).strip())
            tail_words = " ".join(current).split()[-overlap_tokens:]
            current = [" ".join(tail_words)] if tail_words else []
            current_tokens = rough_tokens(current[0]) if current else 0
        current.append(block)
        current_tokens += block_tokens
    if current:
        chunks.append("\n\n".join(current).strip())
    return [c for c in chunks if c.strip()]


def _markitdown_client():
    key, _ = get_openrouter_key()
    if not key:
        return None
    return OpenAI(
        api_key=key,
        base_url=OPENROUTER_URL,
        default_headers={
            "HTTP-Referer": "http://127.0.0.1:5173",
            "X-Title": "FileChat",
        },
    )


def extract_text(path: Path, ext: str) -> str:
    if ext in {"txt", "md", "csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    try:
        from markitdown import MarkItDown

        app_settings = current_app_settings()
        md = MarkItDown(
            enable_plugins=True,
            llm_client=_markitdown_client(),
            llm_model=app_settings["ocr_model"],
        )
        result = md.convert(str(path))
        text = getattr(result, "text_content", "") or ""
    except Exception as exc:
        raise RuntimeError(f"Could not extract document text: {exc}") from exc

    needs_ocr_confidence = ext in {"png", "jpg", "jpeg", "webp", "tiff", "tif", "bmp", "gif", "pdf"}
    if needs_ocr_confidence and len(text.strip()) < 40 and not get_openrouter_key()[0]:
        raise RuntimeError("OCR-capable extraction requires an OpenRouter API key for scanned or image-heavy files.")
    if not text.strip():
        raise RuntimeError("No readable text was extracted from this file.")
    return text


def user_facing_ingest_error(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
        return f"OpenRouter authentication failed. Check your API key in Settings, then retry indexing. Details: {text}"
    if "401 Unauthorized" in text and "openrouter.ai" in text:
        return f"OpenRouter authentication failed. Check your API key in Settings, then retry indexing. Details: {text}"
    if "OpenRouter API key is not configured" in text:
        return "OpenRouter API key is missing. Add your API key in Settings, then retry indexing."
    return text


async def process_file(file_id: str, session_id: str | None = None) -> None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            return
        conn.execute(
            "UPDATE files SET status = ?, progress = ?, updated_at = ? WHERE id = ?",
            ("reading", 0.12, now(), file_id),
        )

    try:
        path = Path(row["path"])
        text = await asyncio.to_thread(extract_text, path, row["type"].lower())
        artifact = get_settings().resolved_data_dir / "artifacts" / f"{file_id}.md"
        artifact.write_text(text, encoding="utf-8")
        chunks = split_chunks(text)
        if not chunks:
            raise RuntimeError("The extracted document did not contain indexable text.")

        with connect() as conn:
            conn.execute(
                "UPDATE files SET status = ?, progress = ?, artifact_path = ?, updated_at = ? WHERE id = ?",
                ("indexing", 0.45, str(artifact), now(), file_id),
            )
            conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
            for ordinal, content in enumerate(chunks, start=1):
                conn.execute(
                    """
                    INSERT INTO chunks (id, file_id, ordinal, content, location, token_count, hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("chk"),
                        file_id,
                        ordinal,
                        content,
                        f"chunk {ordinal}",
                        rough_tokens(content),
                        sha256_text(content),
                        now(),
                    ),
                )

        app_settings = current_app_settings()
        model = app_settings["embedding_model"]
        with connect() as conn:
            chunk_rows = conn.execute("SELECT id, content FROM chunks WHERE file_id = ? ORDER BY ordinal", (file_id,)).fetchall()
        embedding_warning = None
        try:
            client = OpenRouterClient()
            batch_size = 24
            for start in range(0, len(chunk_rows), batch_size):
                batch = chunk_rows[start : start + batch_size]
                embedding = await client.embedding_result([r["content"] for r in batch], model)
                with connect() as conn:
                    for chunk, vector in zip(batch, embedding.vectors):
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO embeddings (chunk_id, model, dimensions, vector, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (chunk["id"], model, len(vector), __import__("json").dumps(vector), now()),
                        )
                    progress = 0.45 + 0.5 * min(1, (start + len(batch)) / max(1, len(chunk_rows)))
                    conn.execute(
                        "UPDATE files SET progress = ?, updated_at = ? WHERE id = ?",
                        (progress, now(), file_id),
                    )
                if session_id:
                    record_usage_event(
                        session_id=session_id,
                        file_id=file_id,
                        kind="file_embedding",
                        model=embedding.model,
                        usage=embedding.usage,
                    )
        except (OpenRouterMissingKey, OpenRouterResponseError, httpx.HTTPStatusError) as exc:
            embedding_warning = user_facing_ingest_error(exc)
        except Exception as exc:
            if "openrouter.ai" not in str(exc).lower() and "OpenRouter" not in str(exc):
                raise
            embedding_warning = user_facing_ingest_error(exc)

        with connect() as conn:
            conn.execute(
                """
                UPDATE files
                SET status = ?, progress = ?, chunk_count = ?, page_count = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                ("ready", 1.0, len(chunks), len(chunks), embedding_warning, now(), file_id),
            )
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                "UPDATE files SET status = ?, progress = ?, error = ?, updated_at = ? WHERE id = ?",
                ("failed", 1.0, user_facing_ingest_error(exc), now(), file_id),
            )
