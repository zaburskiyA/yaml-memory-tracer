import asyncio
import dataclasses
import inspect
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pydantic import BaseModel

from memory_tracer import TracerConfigurationError, install, install_from_env
from memory_tracer.exporter import JsonlExporter
from memory_tracer.report import (
    ReportError,
    group_traces,
    load_events,
    render_dynamic_report,
    render_report,
)
from memory_tracer.serializer import safe_serialize
from memory_tracer.viewer import TraceSnapshotCache, load_live_events
from tests import tracer_targets


def _write_config(directory: Path, content: str) -> Path:
    path = directory / "tracing.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _config(directory: Path, methods: str, boundaries: str = "") -> Path:
    return _write_config(
        directory,
        f"""version: 1
output:
  type: jsonl
  path: traces/events.jsonl
boundaries:
{boundaries or '  []'}
memory:
  - target: tests.tracer_targets.SampleMemory
    methods:
{methods}
""",
    )


def _events(directory: Path) -> list[dict]:
    path = directory / "traces" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class Payload(BaseModel):
    created_at: datetime
    identifier: object
    api_key: str


@dataclasses.dataclass
class DataClassPayload:
    value: str


class MemoryTracerTests(unittest.TestCase):
    def test_report_groups_memory_changes_and_escapes_html(self) -> None:
        events = [
            {
                "trace_id": "trace-one",
                "method": "get_working",
                "category": "read",
                "status": "ok",
                "started_at": "2026-01-01T00:00:00Z",
                "duration_ms": 1,
                "arguments": {"user_id": "1001", "session_id": "session-one"},
                "result": {"messages": [{"role": "user", "content": "</script>"}]},
            },
            {
                "trace_id": "trace-one",
                "method": "save_semantics",
                "category": "write",
                "status": "ok",
                "started_at": "2026-01-01T00:00:01Z",
                "duration_ms": 2,
                "arguments": {"facts": [{"fact": "blue", "confidence": 0.8}]},
                "result": None,
            },
            {
                "trace_id": "trace-one",
                "method": "clear_working",
                "category": "delete",
                "status": "ok",
                "started_at": "2026-01-01T00:00:02Z",
                "duration_ms": 1,
                "arguments": {"user_id": "1001", "session_id": "session-one"},
                "result": None,
            },
        ]

        traces = group_traces(events)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["session_id"], "session-one")
        self.assertEqual(traces[0]["working_messages"][0]["content"], "</script>")
        self.assertEqual(traces[0]["facts"][0]["fact"], "blue")
        self.assertTrue(traces[0]["cleared"])
        report = render_report(events)
        self.assertEqual(report.count("</script>"), 1)
        self.assertIn("\\u003c/script\\u003e", report)
        self.assertIn('id="session-select"', report)
        self.assertIn('id="trace-select"', report)
        self.assertIn("function renderSession", report)
        self.assertIn("const snapshotUrl = null", report)

        dynamic_report = render_dynamic_report("/memory-traces/api/snapshot")
        self.assertIn('const snapshotUrl = "/memory-traces/api/snapshot"', dynamic_report)
        self.assertIn('id="auto-refresh"', dynamic_report)

    def test_report_rejects_invalid_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.jsonl"
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(ReportError, "Invalid JSON"):
                load_events(path)

    def test_live_reader_ignores_only_incomplete_final_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            event = {"trace_id": "one", "method": "read"}
            path.write_bytes(
                (json.dumps(event) + "\n" + '{"trace_id":"unfinished').encode("utf-8")
            )
            self.assertEqual(load_live_events(path), [event])

            path.write_text("not-json\n" + json.dumps(event) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ReportError, "Invalid JSON"):
                load_live_events(path)

    def test_live_snapshot_changes_when_jsonl_grows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            first = {
                "trace_id": "one",
                "method": "build_context",
                "started_at": "2026-01-01T00:00:00Z",
                "arguments": {"session_id": "session-one"},
            }
            second = {
                "trace_id": "two",
                "method": "append_turn",
                "started_at": "2026-01-01T00:00:01Z",
                "arguments": {"session_id": "session-one"},
            }
            path.write_text(json.dumps(first) + "\n", encoding="utf-8")
            cache = TraceSnapshotCache(path)
            before = cache.get()
            with path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(second) + "\n")
            after = cache.get()

            self.assertNotEqual(before["revision"], after["revision"])
            self.assertEqual(after["event_count"], 2)
            self.assertEqual(len(after["traces"]), 2)

    def test_sync_boundary_nesting_arguments_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = _config(
                directory,
                "      echo: read\n      outer: transform",
                "  - target: tests.tracer_targets.boundary_sync",
            )
            original_boundary = tracer_targets.boundary_sync
            installation = install(config)
            self.addCleanup(installation.uninstall)

            secret = "Bearer abc.def.ghi"
            value = {"authorization": secret, "text": "x" * 4_010}
            result = tracer_targets.boundary_sync(tracer_targets.SampleMemory(), value)

            self.assertIs(result, value)
            events = _events(directory)
            self.assertEqual([event["method"] for event in events], ["echo", "outer"])
            self.assertEqual(events[0]["trace_id"], events[1]["trace_id"])
            self.assertEqual(events[0]["parent_event_id"], events[1]["event_id"])
            self.assertIsNone(events[1]["parent_event_id"])
            self.assertEqual(events[0]["arguments"]["value"]["authorization"], "[REDACTED]")
            self.assertIn("<truncated 10 chars>", events[0]["arguments"]["value"]["text"])
            self.assertEqual(events[0]["arguments"]["optional"], "default")
            self.assertEqual(events[0]["status"], "ok")
            self.assertGreaterEqual(events[0]["duration_ms"], 0)

            installation.uninstall()
            self.assertIs(tracer_targets.boundary_sync, original_boundary)
            installation.uninstall()

    def test_exception_is_recorded_and_re_raised(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            installation = install(_config(directory, "      fail: write"))
            self.addCleanup(installation.uninstall)

            with self.assertRaisesRegex(LookupError, "do not hide me"):
                tracer_targets.SampleMemory().fail("do not hide me")

            event = _events(directory)[0]
            self.assertEqual(event["status"], "error")
            self.assertEqual(event["error"]["type"], "builtins.LookupError")
            self.assertEqual(event["error"]["message"], "do not hide me")
            self.assertIsNone(event["result"])

    def test_async_contexts_are_isolated(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                installation = install(
                    _config(
                        directory,
                        "      async_echo: read",
                        "  - target: tests.tracer_targets.boundary_async",
                    )
                )
                try:
                    first, second = await asyncio.gather(
                        tracer_targets.boundary_async(tracer_targets.SampleMemory(), "first", 0.02),
                        tracer_targets.boundary_async(tracer_targets.SampleMemory(), "second", 0),
                    )
                    self.assertEqual((first, second), ("first", "second"))
                    events = _events(directory)
                    self.assertEqual(len(events), 2)
                    self.assertEqual(len({event["trace_id"] for event in events}), 2)
                    self.assertTrue(all(event["parent_event_id"] is None for event in events))
                finally:
                    installation.uninstall()

        asyncio.run(scenario())

    def test_safe_serialization_supported_types_and_limits(self) -> None:
        identifier = uuid4()
        payload = Payload(
            created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            identifier=identifier,
            api_key="sk-example-secret-value",
        )
        serialized = safe_serialize(
            {
                "model": payload,
                "dataclass": DataClassPayload("ok"),
                "items": list(range(105)),
                "unknown": object(),
            }
        )

        self.assertEqual(serialized["model"]["identifier"], str(identifier))
        self.assertEqual(serialized["model"]["api_key"], "[REDACTED]")
        self.assertEqual(serialized["dataclass"], {"value": "ok"})
        self.assertEqual(serialized["items"][-1], {"__truncated_items__": 5})
        self.assertEqual(serialized["unknown"]["__type__"], "builtins.object")

    def test_invalid_config_and_duplicate_install_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            invalid = _write_config(
                directory,
                """version: 1
output: {type: jsonl, path: events.jsonl}
memory:
  - target: tests.tracer_targets.SampleMemory
    methods: {echo: invalid-category}
""",
            )
            with self.assertRaisesRegex(TracerConfigurationError, "Invalid memory tracer config"):
                install(invalid)

            valid = _config(directory, "      echo: read")
            installation = install(valid)
            try:
                with self.assertRaisesRegex(TracerConfigurationError, "already wrapped"):
                    install(valid)
            finally:
                installation.uninstall()

    def test_missing_target_fails_before_any_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original_echo = tracer_targets.SampleMemory.echo
            config = _write_config(
                directory,
                """version: 1
output: {type: jsonl, path: events.jsonl}
boundaries:
  - target: tests.tracer_targets.missing_boundary
memory:
  - target: tests.tracer_targets.SampleMemory
    methods: {echo: read}
""",
            )
            with self.assertRaisesRegex(TracerConfigurationError, "does not exist"):
                install(config)
            self.assertIs(tracer_targets.SampleMemory.echo, original_echo)

    def test_install_from_env_is_optional_and_preserves_signature(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(install_from_env())

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = _config(directory, "      echo: read")
            original_signature = inspect.signature(tracer_targets.SampleMemory.echo)
            with patch.dict(os.environ, {"MEMORY_TRACING_CONFIG": str(config)}):
                installation = install_from_env()
            self.assertIsNotNone(installation)
            try:
                self.assertEqual(inspect.signature(tracer_targets.SampleMemory.echo), original_signature)
            finally:
                installation.uninstall()

    def test_exporter_runtime_failure_does_not_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            exporter = JsonlExporter(Path(temporary) / "events.jsonl")
            event = {"event_id": "one"}
            with (
                self.assertLogs("memory_tracer.exporter", level="ERROR") as captured,
                patch("pathlib.Path.open", side_effect=OSError("disk full")),
            ):
                exporter.export(event)
            self.assertIn("disk full", captured.output[0])


if __name__ == "__main__":
    unittest.main()
