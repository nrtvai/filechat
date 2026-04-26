from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .database import connect
from .utils import new_id, now


@dataclass
class UsageInfo:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cost: float = 0.0
    completion_cost: float = 0.0
    total_cost: float = 0.0


def _number(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def usage_from_response(payload: dict[str, Any], *, pricing: dict[str, Any] | None = None) -> UsageInfo:
    usage = payload.get("usage") or {}
    prompt_tokens = int(_number(usage.get("prompt_tokens")))
    completion_tokens = int(_number(usage.get("completion_tokens")))
    total_tokens = int(_number(usage.get("total_tokens"))) or prompt_tokens + completion_tokens

    cost_details = usage.get("cost_details") or {}
    prompt_cost = _number(cost_details.get("upstream_inference_prompt_cost"))
    completion_cost = _number(cost_details.get("upstream_inference_completions_cost"))

    pricing = pricing or {}
    estimated_prompt_cost = prompt_tokens * _number(pricing.get("prompt"))
    estimated_completion_cost = completion_tokens * _number(pricing.get("completion"))
    response_total_cost = _number(usage.get("cost"))

    if prompt_cost == 0 and completion_cost == 0:
        estimated_total = estimated_prompt_cost + estimated_completion_cost
        if response_total_cost > 0 and estimated_total > 0:
            prompt_cost = response_total_cost * (estimated_prompt_cost / estimated_total)
            completion_cost = response_total_cost * (estimated_completion_cost / estimated_total)
        elif response_total_cost > 0:
            if completion_tokens > 0 and prompt_tokens > 0:
                prompt_cost = response_total_cost / 2
                completion_cost = response_total_cost / 2
            elif completion_tokens > 0:
                completion_cost = response_total_cost
            else:
                prompt_cost = response_total_cost
        else:
            prompt_cost = estimated_prompt_cost
            completion_cost = estimated_completion_cost

    total_cost = response_total_cost or prompt_cost + completion_cost
    return UsageInfo(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cost=prompt_cost,
        completion_cost=completion_cost,
        total_cost=total_cost,
    )


def record_usage_event(
    *,
    session_id: str,
    kind: str,
    model: str,
    usage: UsageInfo,
    message_id: str | None = None,
    file_id: str | None = None,
) -> str:
    event_id = new_id("use")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO usage_events
            (id, session_id, message_id, file_id, kind, model, prompt_tokens,
             completion_tokens, total_tokens, prompt_cost, completion_cost, total_cost, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                message_id,
                file_id,
                kind,
                model,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                usage.prompt_cost,
                usage.completion_cost,
                usage.total_cost,
                now(),
            ),
        )
    return event_id


def usage_for_message(message_id: str) -> UsageInfo:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(prompt_tokens), 0) prompt_tokens,
              COALESCE(SUM(completion_tokens), 0) completion_tokens,
              COALESCE(SUM(total_tokens), 0) total_tokens,
              COALESCE(SUM(prompt_cost), 0) prompt_cost,
              COALESCE(SUM(completion_cost), 0) completion_cost,
              COALESCE(SUM(total_cost), 0) total_cost
            FROM usage_events
            WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()
    return UsageInfo(
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        total_tokens=row["total_tokens"],
        prompt_cost=row["prompt_cost"],
        completion_cost=row["completion_cost"],
        total_cost=row["total_cost"],
    )


def usage_for_file(session_id: str, file_id: str) -> UsageInfo:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(prompt_tokens), 0) prompt_tokens,
              COALESCE(SUM(completion_tokens), 0) completion_tokens,
              COALESCE(SUM(total_tokens), 0) total_tokens,
              COALESCE(SUM(prompt_cost), 0) prompt_cost,
              COALESCE(SUM(completion_cost), 0) completion_cost,
              COALESCE(SUM(total_cost), 0) total_cost
            FROM usage_events
            WHERE session_id = ? AND file_id = ?
            """,
            (session_id, file_id),
        ).fetchone()
    return UsageInfo(
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        total_tokens=row["total_tokens"],
        prompt_cost=row["prompt_cost"],
        completion_cost=row["completion_cost"],
        total_cost=row["total_cost"],
    )


def usage_summary(session_id: str) -> dict[str, float | int]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT kind,
              COALESCE(SUM(prompt_tokens), 0) prompt_tokens,
              COALESCE(SUM(completion_tokens), 0) completion_tokens,
              COALESCE(SUM(total_tokens), 0) total_tokens,
              COALESCE(SUM(prompt_cost), 0) prompt_cost,
              COALESCE(SUM(completion_cost), 0) completion_cost,
              COALESCE(SUM(total_cost), 0) total_cost
            FROM usage_events
            WHERE session_id = ?
            GROUP BY kind
            """,
            (session_id,),
        ).fetchall()

    summary = {
        "chat_prompt_tokens": 0,
        "chat_completion_tokens": 0,
        "embedding_tokens": 0,
        "chat_prompt_cost": 0.0,
        "chat_completion_cost": 0.0,
        "embedding_cost": 0.0,
        "total_tokens": 0,
        "total_cost": 0.0,
    }
    for row in rows:
        kind = row["kind"]
        summary["total_tokens"] += row["total_tokens"]
        summary["total_cost"] += row["total_cost"]
        if kind in {"chat_prompt", "chat_completion"}:
            summary["chat_prompt_tokens"] += row["prompt_tokens"]
            summary["chat_completion_tokens"] += row["completion_tokens"]
            summary["chat_prompt_cost"] += row["prompt_cost"]
            summary["chat_completion_cost"] += row["completion_cost"]
        elif kind in {"query_embedding", "file_embedding"}:
            summary["embedding_tokens"] += row["total_tokens"]
            summary["embedding_cost"] += row["total_cost"]
    return summary
