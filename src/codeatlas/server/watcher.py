"""Pure-Python async file watcher and event bus for live architecture updates."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import logging
import os
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codeatlas.models.analysis import AnalysisResult
from codeatlas.models.config import ScanConfig
from codeatlas.scanner.walker import walk_repository

_HASH_CHUNK_BYTES = 65_536
_SHA256_PREFIX_LEN = 16


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """Lightweight immutable snapshot of a repository source file."""

    mtime_ns: int
    size: int
    sha256_prefix: str


def _default_scan_repository(root_path: Path, config: ScanConfig) -> AnalysisResult:
    from codeatlas.analysis.engine import scan_repository

    return scan_repository(root_path, config=config)


def _compute_sha256_prefix(file_path: Path, prefix_len: int = _SHA256_PREFIX_LEN) -> str:
    """Compute a hex SHA-256 prefix for ``file_path`` content."""
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()[:prefix_len]


def take_snapshot(
    root_path: str | Path,
    config: ScanConfig | None = None,
) -> dict[str, FileSnapshot]:
    """Walk supported repository files and build deterministic file snapshots."""
    cfg = config or ScanConfig()
    walk_result = walk_repository(root_path, cfg, supported_only=True)
    snapshots: dict[str, FileSnapshot] = {}

    for walked in walk_result.files:
        try:
            st = os.stat(walked.path, follow_symlinks=True)
            sha_prefix = _compute_sha256_prefix(walked.path)
        except OSError:
            continue
        snapshots[walked.relative_path] = FileSnapshot(
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
            sha256_prefix=sha_prefix,
        )

    return snapshots


def diff_snapshots(
    previous: dict[str, FileSnapshot],
    current: dict[str, FileSnapshot],
) -> list[str]:
    """Return sorted POSIX relative paths added, deleted, or modified between snapshots."""
    changed: set[str] = set()
    for rel_path, snap in current.items():
        if previous.get(rel_path) != snap:
            changed.add(rel_path)
    for rel_path in previous:
        if rel_path not in current:
            changed.add(rel_path)
    return sorted(changed)


class EventBus:
    """Async pub/sub event bus with bounded history and immediate shutdown wakeup."""

    def __init__(
        self,
        *,
        history_limit: int = 50,
        queue_maxsize: int = 128,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        if history_limit < 0:
            raise ValueError(f"history_limit must be >= 0, got {history_limit}")
        if queue_maxsize < 1:
            raise ValueError(f"queue_maxsize must be >= 1, got {queue_maxsize}")
        self._history: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._queue_maxsize = queue_maxsize
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._shutdown_event = shutdown_event or asyncio.Event()
        self._closed = False

    @property
    def shutdown_event(self) -> asyncio.Event:
        """Return the shared shutdown signal used by SSE streams."""
        return self._shutdown_event

    @property
    def subscriber_count(self) -> int:
        """Return the current number of active subscribers."""
        return len(self._subscribers)

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return a snapshot list of retained recent events."""
        return list(self._history)

    @property
    def is_closed(self) -> bool:
        """Return True when the event bus has been closed."""
        return self._closed

    def reopen(self) -> None:
        """Clear closed/shutdown flags when restarting an application lifespan."""
        self._closed = False
        self._shutdown_event.clear()

    def subscribe(self, *, replay_history: bool = False) -> asyncio.Queue[dict[str, Any] | None]:
        """Register a bounded subscriber queue and optionally replay retained history."""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=self._queue_maxsize)
        if replay_history:
            for item in self._history:
                self._enqueue_drop_oldest(queue, item)
        if self._closed:
            self._enqueue_drop_oldest(queue, None)
            return queue
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        """Remove ``queue`` from active subscribers."""
        self._subscribers.discard(queue)

    def publish_nowait(self, event: dict[str, Any]) -> None:
        """Append ``event`` to bounded history and push it to all subscribers."""
        if self._closed:
            return
        self._history.append(event)
        for queue in tuple(self._subscribers):
            self._enqueue_drop_oldest(queue, event)

    async def publish(self, event: dict[str, Any]) -> None:
        """Publish ``event`` asynchronously to all active subscribers."""
        self.publish_nowait(event)

    def close(self) -> None:
        """Close the bus, set shutdown_event, and wake all waiting subscribers immediately."""
        self._closed = True
        self._shutdown_event.set()
        for queue in tuple(self._subscribers):
            self._enqueue_drop_oldest(queue, None)

    @staticmethod
    def _enqueue_drop_oldest(
        queue: asyncio.Queue[dict[str, Any] | None],
        item: dict[str, Any] | None,
    ) -> None:
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(item)


class RepoWatcher:
    """Async polling watcher detecting source file changes and broadcasting rescans."""

    def __init__(
        self,
        root_path: str | Path,
        event_bus: EventBus,
        *,
        scan_config: ScanConfig | None = None,
        poll_interval: float = 0.8,
        scan_fn: Callable[[Path, ScanConfig], AnalysisResult] | None = None,
        on_update: Callable[[AnalysisResult, list[str]], Awaitable[None] | None] | None = None,
        shutdown_event: asyncio.Event | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError(f"poll_interval must be > 0, got {poll_interval}")
        self._root_path = Path(root_path).resolve()
        self._event_bus = event_bus
        self._scan_config = scan_config or ScanConfig()
        self._poll_interval = poll_interval
        self._scan_fn = scan_fn or _default_scan_repository
        self._on_update = on_update
        self._shutdown_event = shutdown_event or event_bus.shutdown_event
        self._logger = logger or logging.getLogger("codeatlas.server.watcher")
        self._snapshot: dict[str, FileSnapshot] | None = None
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Return True while the background watch task is active."""
        return self._task is not None and not self._task.done()

    async def prime(self) -> dict[str, FileSnapshot]:
        """Capture and store the baseline snapshot without emitting an update event."""
        snapshot = await asyncio.to_thread(take_snapshot, self._root_path, self._scan_config)
        self._snapshot = snapshot
        return snapshot

    async def poll_once(self) -> list[str]:
        """Check for file changes once, rescanning and publishing if files changed."""
        current = await asyncio.to_thread(take_snapshot, self._root_path, self._scan_config)
        if self._snapshot is None:
            self._snapshot = current
            return []

        changed = diff_snapshots(self._snapshot, current)
        self._snapshot = current
        if not changed:
            return []

        result = await asyncio.to_thread(self._scan_fn, self._root_path, self._scan_config)
        if self._on_update is not None:
            maybe_awaitable = self._on_update(result, changed)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

        event: dict[str, Any] = {
            "type": "graph_updated",
            "graph": result.graph.model_dump(mode="json"),
            "health": result.health.model_dump(mode="json"),
            "changed_files": changed,
            "timestamp": datetime.now(UTC).isoformat(),
            "watch_enabled": True,
            "repo_name": result.graph.repo_name,
        }
        await self._event_bus.publish(event)
        return changed

    async def start(self) -> None:
        """Prime baseline snapshot and launch the background polling task."""
        if self.is_running:
            return
        self._stop_event.clear()
        if self._snapshot is None:
            await self.prime()
        self._task = asyncio.create_task(self._watch_loop(), name="codeatlas-repo-watcher")

    async def stop(self) -> None:
        """Stop the background polling task cleanly."""
        self._stop_event.set()
        if self._task is None:
            return
        task = self._task
        self._task = None
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _watch_loop(self) -> None:
        while not self._stop_event.is_set() and not self._shutdown_event.is_set():
            stop_wait = asyncio.create_task(self._stop_event.wait())
            shutdown_wait = asyncio.create_task(self._shutdown_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {stop_wait, shutdown_wait},
                    timeout=self._poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                stop_wait.cancel()
                shutdown_wait.cancel()
                raise

            for pending_task in pending:
                pending_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending_task

            if done:
                break

            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning("Watcher poll failed: %s", exc)
