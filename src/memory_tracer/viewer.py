"""Read-only HTTP viewer for a JSONL memory trace."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .report import ReportError, group_traces, render_dynamic_report


def load_live_events(path: str | Path) -> list[dict[str, Any]]:
    """Read complete JSONL records, ignoring a final record still being written."""
    source = Path(path)
    try:
        raw = source.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ReportError(f"Cannot read {source}: {exc}") from exc

    if raw and not raw.endswith(b"\n"):
        raw = raw.rpartition(b"\n")[0]
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ReportError(f"Invalid UTF-8 at {source}: {exc}") from exc

    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReportError(f"Invalid JSON at {source}:{line_number}: {exc.msg}") from exc
        if not isinstance(event, dict) or not event.get("trace_id"):
            raise ReportError(f"Missing trace_id at {source}:{line_number}")
        events.append(event)
    return events


class TraceSnapshotCache:
    """Cache the grouped view until the trace file changes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._fingerprint: tuple[int, int] | None = None
        self._snapshot: dict[str, Any] = {
            "revision": "missing",
            "updated_at": None,
            "event_count": 0,
            "traces": [],
            "error": None,
        }

    def get(self) -> dict[str, Any]:
        with self._lock:
            try:
                stat = self.path.stat()
            except FileNotFoundError:
                self._fingerprint = None
                self._snapshot = {
                    "revision": "missing",
                    "updated_at": None,
                    "event_count": 0,
                    "traces": [],
                    "error": None,
                }
                return self._snapshot
            except OSError as exc:
                return {**self._snapshot, "error": f"Cannot inspect {self.path}: {exc}"}

            fingerprint = (stat.st_size, stat.st_mtime_ns)
            if fingerprint == self._fingerprint:
                return self._snapshot

            try:
                events = load_live_events(self.path)
            except ReportError as exc:
                return {**self._snapshot, "error": str(exc)}

            self._fingerprint = fingerprint
            self._snapshot = {
                "revision": f"{stat.st_size}-{stat.st_mtime_ns}",
                "updated_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat().replace("+00:00", "Z"),
                "event_count": len(events),
                "traces": group_traces(events),
                "error": None,
            }
            return self._snapshot


def create_viewer_router(path: str | Path) -> APIRouter:
    cache = TraceSnapshotCache(path)
    router = APIRouter()

    @router.get("/memory-traces", response_class=HTMLResponse)
    def viewer_page() -> str:
        return render_dynamic_report("/memory-traces/api/snapshot")

    @router.get("/memory-traces/api/snapshot")
    def viewer_snapshot(if_none_match: str | None = Header(default=None)) -> Response:
        snapshot = cache.get()
        etag = f'"{snapshot["revision"]}"'
        headers = {"ETag": etag, "Cache-Control": "no-store"}
        if snapshot["error"] is None and if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(snapshot, headers=headers)

    return router
