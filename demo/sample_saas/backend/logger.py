"""Structured JSON-line style logger shared across SampleSaaS backend modules."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class StructuredLogger:
    """Thin structured logger wrapper emitting consistent key-value records."""

    def __init__(self, component: str, level: int = logging.INFO) -> None:
        self.component = component
        self._logger = logging.getLogger(f"sample_saas.{component}")
        self._logger.setLevel(level)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
            self._logger.propagate = False

    def _emit(self, level: int, event: str, fields: dict[str, object]) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": self.component,
            "event": event,
            "fields": {key: str(value) for key, value in fields.items()},
        }
        self._logger.log(level, json.dumps(payload, sort_keys=True))

    def info(self, event: str, **fields: object) -> None:
        """Emit an informational structured log event."""
        self._emit(logging.INFO, event, fields)

    def warning(self, event: str, **fields: object) -> None:
        """Emit a warning structured log event."""
        self._emit(logging.WARNING, event, fields)

    def error(self, event: str, **fields: object) -> None:
        """Emit an error structured log event."""
        self._emit(logging.ERROR, event, fields)


def get_logger(component: str) -> StructuredLogger:
    """Create a component-scoped structured logger."""
    return StructuredLogger(component=component)
