"""Bounded and secret-aware conversion of values to JSON-compatible data."""

import dataclasses
import itertools
import math
import re
from datetime import date, datetime, time
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

MAX_STRING_LENGTH = 4_000
MAX_COLLECTION_LENGTH = 100
MAX_DEPTH = 6
MAX_REPR_LENGTH = 1_000

_SECRET_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "secret",
    "client_secret",
}
_TOKEN_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)


def _redact_text(value: str) -> str:
    result = value
    for pattern in _TOKEN_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}...<truncated {omitted} chars>"


def _safe_serialize(value: Any, *, _depth: int = 0) -> Any:
    if _depth > MAX_DEPTH:
        return {"__truncated__": "max_depth", "type": type(value).__name__}
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"__float__": repr(value)}
    if isinstance(value, str):
        return _redact_text(_truncate(value, MAX_STRING_LENGTH))
    if isinstance(value, bytes):
        return {"__bytes__": len(value)}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return safe_serialize(value.value, _depth=_depth + 1)
    if isinstance(value, BaseModel):
        return safe_serialize(value.model_dump(mode="python"), _depth=_depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
        return safe_serialize(fields, _depth=_depth + 1)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        items = itertools.islice(value.items(), MAX_COLLECTION_LENGTH)
        for key, item in items:
            string_key = str(key)
            if string_key.casefold() in _SECRET_KEYS:
                result[string_key] = "[REDACTED]"
            else:
                result[string_key] = safe_serialize(item, _depth=_depth + 1)
        if len(value) > MAX_COLLECTION_LENGTH:
            result["__truncated_items__"] = len(value) - MAX_COLLECTION_LENGTH
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(itertools.islice(value, MAX_COLLECTION_LENGTH))
        result = [
            safe_serialize(item, _depth=_depth + 1)
            for item in items
        ]
        if len(value) > MAX_COLLECTION_LENGTH:
            result.append({"__truncated_items__": len(value) - MAX_COLLECTION_LENGTH})
        return result

    try:
        rendered = repr(value)
    except Exception:  # noqa: BLE001 - arbitrary application objects must not break tracing
        rendered = f"<{type(value).__module__}.{type(value).__qualname__}>"
    return {
        "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr": _redact_text(_truncate(rendered, MAX_REPR_LENGTH)),
    }


def safe_serialize(value: Any, *, _depth: int = 0) -> Any:
    """Return bounded JSON data without leaking serialization failures to the caller."""
    try:
        return _safe_serialize(value, _depth=_depth)
    except Exception as exc:  # noqa: BLE001 - serialization is best-effort by contract
        return {
            "__serialization_error__": _redact_text(_truncate(str(exc), MAX_REPR_LENGTH)),
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
        }
