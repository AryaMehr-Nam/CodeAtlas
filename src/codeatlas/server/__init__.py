"""Hardened localhost Starlette server and pure-Python async repository watcher."""

from __future__ import annotations

from codeatlas.server.app import (
    CONTENT_SECURITY_POLICY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_PREVIEW_BYTES,
    create_app,
    serve_studio,
    validate_loopback_host,
)
from codeatlas.server.watcher import (
    EventBus,
    FileSnapshot,
    RepoWatcher,
    diff_snapshots,
    take_snapshot,
)

__all__ = [
    "CONTENT_SECURITY_POLICY",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_PREVIEW_BYTES",
    "EventBus",
    "FileSnapshot",
    "RepoWatcher",
    "create_app",
    "diff_snapshots",
    "serve_studio",
    "take_snapshot",
    "validate_loopback_host",
]
