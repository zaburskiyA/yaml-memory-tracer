"""Context propagation and transparent wrappers."""

import functools
import inspect
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from memory_tracer.exporter import JsonlExporter
from memory_tracer.serializer import safe_serialize

_current_trace_id: ContextVar[str | None] = ContextVar("memory_trace_id", default=None)
_current_event_id: ContextVar[str | None] = ContextVar("memory_event_id", default=None)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _arguments(function: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    try:
        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
        values.pop("self", None)
        values.pop("cls", None)
        return safe_serialize(values)
    except Exception as exc:  # noqa: BLE001 - argument capture must not affect the call
        return {
            "args": safe_serialize(args[1:] if args else args),
            "kwargs": safe_serialize(kwargs),
            "binding_error": safe_serialize(str(exc)),
        }


class MemoryTracer:
    def __init__(self, exporter: JsonlExporter):
        self.exporter = exporter

    def wrap_boundary(self, function: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def async_boundary(*args: Any, **kwargs: Any) -> Any:
                trace_token = _current_trace_id.set(str(uuid4()))
                event_token = _current_event_id.set(None)
                try:
                    return await function(*args, **kwargs)
                finally:
                    _current_event_id.reset(event_token)
                    _current_trace_id.reset(trace_token)

            return async_boundary

        @functools.wraps(function)
        def sync_boundary(*args: Any, **kwargs: Any) -> Any:
            trace_token = _current_trace_id.set(str(uuid4()))
            event_token = _current_event_id.set(None)
            try:
                return function(*args, **kwargs)
            finally:
                _current_event_id.reset(event_token)
                _current_trace_id.reset(trace_token)

        return sync_boundary

    def wrap_memory_method(
        self,
        function: Callable[..., Any],
        *,
        target: str,
        method: str,
        category: str,
    ) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def async_memory(*args: Any, **kwargs: Any) -> Any:
                return await self._run_async(function, target, method, category, args, kwargs)

            return async_memory

        @functools.wraps(function)
        def sync_memory(*args: Any, **kwargs: Any) -> Any:
            return self._run_sync(function, target, method, category, args, kwargs)

        return sync_memory

    def _start_event(
        self,
        function: Callable[..., Any],
        target: str,
        method: str,
        category: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[dict[str, Any], int, object, object]:
        trace_id = _current_trace_id.get()
        trace_token = None
        if trace_id is None:
            trace_id = str(uuid4())
            trace_token = _current_trace_id.set(trace_id)
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "trace_id": trace_id,
            "parent_event_id": _current_event_id.get(),
            "target": target,
            "method": method,
            "category": category,
            "started_at": _utc_now(),
            "ended_at": None,
            "duration_ms": None,
            "status": "ok",
            "arguments": _arguments(function, args, kwargs),
            "result": None,
            "error": None,
        }
        event_token = _current_event_id.set(event_id)
        return event, time.perf_counter_ns(), event_token, trace_token

    def _finish_event(
        self,
        event: dict[str, Any],
        started_ns: int,
        event_token: object,
        trace_token: object,
    ) -> None:
        event["ended_at"] = _utc_now()
        event["duration_ms"] = round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)
        _current_event_id.reset(event_token)
        if trace_token is not None:
            _current_trace_id.reset(trace_token)
        self.exporter.export(event)

    def _run_sync(
        self,
        function: Callable[..., Any],
        target: str,
        method: str,
        category: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        event, started_ns, event_token, trace_token = self._start_event(
            function, target, method, category, args, kwargs
        )
        try:
            result = function(*args, **kwargs)
            event["result"] = safe_serialize(result)
            return result
        except BaseException as exc:
            event["status"] = "error"
            event["error"] = {
                "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "message": safe_serialize(str(exc)),
            }
            raise
        finally:
            self._finish_event(event, started_ns, event_token, trace_token)

    async def _run_async(
        self,
        function: Callable[..., Any],
        target: str,
        method: str,
        category: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        event, started_ns, event_token, trace_token = self._start_event(
            function, target, method, category, args, kwargs
        )
        try:
            result = await function(*args, **kwargs)
            event["result"] = safe_serialize(result)
            return result
        except BaseException as exc:
            event["status"] = "error"
            event["error"] = {
                "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "message": safe_serialize(str(exc)),
            }
            raise
        finally:
            self._finish_event(event, started_ns, event_token, trace_token)
