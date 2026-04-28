from __future__ import annotations

from typing import Any

from .auth import Principal
from .database import connect
from .security import redact_text, sanitize_metadata
from .utils import excerpt, json_dumps, json_loads, new_id, now


def _safe_text(value: str, limit: int = 1000) -> str:
    return excerpt(redact_text(value), limit)


def _node_out(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "owner_user_id": row["owner_user_id"],
        "scope": row["scope"],
        "type": row["type"],
        "title": row["title"],
        "summary": row["summary"],
        "properties": sanitize_metadata(json_loads(row["properties"], {})),
        "source_refs": sanitize_metadata(json_loads(row["source_refs"], [])),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _edge_out(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "source_node_id": row["source_node_id"],
        "target_node_id": row["target_node_id"],
        "relation_type": row["relation_type"],
        "weight": row["weight"],
        "confidence": row["confidence"],
        "properties": sanitize_metadata(json_loads(row["properties"], {})),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _node_access_clause(prefix: str = "") -> str:
    table = f"{prefix}." if prefix else ""
    return f"({table}scope = 'organization' OR {table}owner_user_id = ?)"


def create_node(principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
    node_id = new_id("wkn")
    stamp = now()
    scope = payload.get("scope") or "organization"
    owner_user_id = principal.user_id if scope == "user" else None
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_nodes
            (id, organization_id, owner_user_id, scope, type, title, summary, properties, source_refs, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                principal.organization_id,
                owner_user_id,
                scope,
                _safe_text(str(payload.get("type") or "note"), 80),
                _safe_text(str(payload.get("title") or "Untitled wiki node"), 180),
                _safe_text(str(payload.get("summary") or ""), 2000),
                json_dumps(sanitize_metadata(payload.get("properties") or {})),
                json_dumps(sanitize_metadata(payload.get("source_refs") or [])),
                principal.user_id,
                stamp,
                stamp,
            ),
        )
        row = conn.execute("SELECT * FROM wiki_nodes WHERE id = ?", (node_id,)).fetchone()
    return _node_out(row)


def list_nodes(principal: Principal, *, scope: str | None = None, node_type: str | None = None) -> list[dict[str, Any]]:
    filters = ["organization_id = ?", _node_access_clause()]
    params: list[Any] = [principal.organization_id, principal.user_id]
    if scope:
        filters.append("scope = ?")
        params.append(scope)
    if node_type:
        filters.append("type = ?")
        params.append(node_type)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM wiki_nodes
            WHERE {' AND '.join(filters)}
            ORDER BY updated_at DESC
            LIMIT 200
            """,
            tuple(params),
        ).fetchall()
    return [_node_out(row) for row in rows]


def get_node(principal: Principal, node_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM wiki_nodes
            WHERE id = ? AND organization_id = ? AND {_node_access_clause()}
            """,
            (node_id, principal.organization_id, principal.user_id),
        ).fetchone()
    return _node_out(row) if row else None


def update_node(principal: Principal, node_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    current = get_node(principal, node_id)
    if not current:
        return None
    updates: list[str] = []
    values: list[Any] = []
    if "type" in patch:
        updates.append("type = ?")
        values.append(_safe_text(str(patch["type"]), 80))
    if "title" in patch:
        updates.append("title = ?")
        values.append(_safe_text(str(patch["title"]), 180))
    if "summary" in patch:
        updates.append("summary = ?")
        values.append(_safe_text(str(patch["summary"] or ""), 2000))
    if "properties" in patch:
        updates.append("properties = ?")
        values.append(json_dumps(sanitize_metadata(patch["properties"] or {})))
    if "source_refs" in patch:
        updates.append("source_refs = ?")
        values.append(json_dumps(sanitize_metadata(patch["source_refs"] or [])))
    if not updates:
        return current
    updates.append("updated_at = ?")
    values.append(now())
    values.extend([node_id, principal.organization_id, principal.user_id])
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE wiki_nodes SET {', '.join(updates)}
            WHERE id = ? AND organization_id = ? AND {_node_access_clause()}
            """,
            tuple(values),
        )
    return get_node(principal, node_id)


def delete_node(principal: Principal, node_id: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            f"""
            DELETE FROM wiki_nodes
            WHERE id = ? AND organization_id = ? AND {_node_access_clause()}
            """,
            (node_id, principal.organization_id, principal.user_id),
        )
    return cursor.rowcount > 0


def create_edge(principal: Principal, payload: dict[str, Any]) -> dict[str, Any] | None:
    source = get_node(principal, str(payload.get("source_node_id") or ""))
    target = get_node(principal, str(payload.get("target_node_id") or ""))
    if not source or not target:
        return None
    edge_id = new_id("wke")
    stamp = now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_edges
            (id, organization_id, source_node_id, target_node_id, relation_type, weight, confidence, properties, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                principal.organization_id,
                source["id"],
                target["id"],
                _safe_text(str(payload.get("relation_type") or "related_to"), 80),
                float(payload.get("weight", 1.0)),
                float(payload.get("confidence", 0.0)),
                json_dumps(sanitize_metadata(payload.get("properties") or {})),
                principal.user_id,
                stamp,
                stamp,
            ),
        )
        row = conn.execute("SELECT * FROM wiki_edges WHERE id = ?", (edge_id,)).fetchone()
    return _edge_out(row)


def list_edges(principal: Principal) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT e.* FROM wiki_edges e
            JOIN wiki_nodes s ON s.id = e.source_node_id
            JOIN wiki_nodes t ON t.id = e.target_node_id
            WHERE e.organization_id = ?
              AND {_node_access_clause('s')}
              AND {_node_access_clause('t')}
            ORDER BY e.updated_at DESC
            LIMIT 200
            """,
            (principal.organization_id, principal.user_id, principal.user_id),
        ).fetchall()
    return [_edge_out(row) for row in rows]


def get_edge(principal: Principal, edge_id: str) -> dict[str, Any] | None:
    return next((edge for edge in list_edges(principal) if edge["id"] == edge_id), None)


def update_edge(principal: Principal, edge_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    if not get_edge(principal, edge_id):
        return None
    updates: list[str] = []
    values: list[Any] = []
    if "relation_type" in patch:
        updates.append("relation_type = ?")
        values.append(_safe_text(str(patch["relation_type"]), 80))
    if "weight" in patch:
        updates.append("weight = ?")
        values.append(float(patch["weight"]))
    if "confidence" in patch:
        updates.append("confidence = ?")
        values.append(float(patch["confidence"]))
    if "properties" in patch:
        updates.append("properties = ?")
        values.append(json_dumps(sanitize_metadata(patch["properties"] or {})))
    if not updates:
        return get_edge(principal, edge_id)
    updates.append("updated_at = ?")
    values.append(now())
    values.extend([edge_id, principal.organization_id])
    with connect() as conn:
        conn.execute(
            f"UPDATE wiki_edges SET {', '.join(updates)} WHERE id = ? AND organization_id = ?",
            tuple(values),
        )
    return get_edge(principal, edge_id)


def delete_edge(principal: Principal, edge_id: str) -> bool:
    if not get_edge(principal, edge_id):
        return False
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM wiki_edges WHERE id = ? AND organization_id = ?",
            (edge_id, principal.organization_id),
        )
    return cursor.rowcount > 0
