"""JSON-lines event stream from a worker to its orchestrator (stdout)."""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from typing import Any, TextIO


class Emitter:
    """Writes one JSON object per line and flushes after every event."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()

    def emit(self, ev: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "ev": ev,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()
