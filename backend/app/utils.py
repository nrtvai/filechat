from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"))


def json_loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def extension(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".") or "txt"


def rough_tokens(text: str) -> int:
    return max(1, math.ceil(len(re.findall(r"\S+", text)) * 1.25))


def excerpt(text: str, limit: int = 320) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    av = list(a)
    bv = list(b)
    if len(av) != len(bv) or not av:
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv))
    amag = math.sqrt(sum(x * x for x in av))
    bmag = math.sqrt(sum(y * y for y in bv))
    if amag == 0 or bmag == 0:
        return 0.0
    return dot / (amag * bmag)
