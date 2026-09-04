"""JSON Lines event exporter."""

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class JsonlExporter:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8"):
                pass
        except OSError as exc:
            raise ValueError(f"Cannot open memory trace output {path}: {exc}") from exc

    def export(self, event: dict[str, Any]) -> None:
        try:
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self._lock, self.path.open("a", encoding="utf-8") as output:
                output.write(line)
                output.flush()
        except Exception:
            logger.exception("Failed to write memory trace event to %s", self.path)
