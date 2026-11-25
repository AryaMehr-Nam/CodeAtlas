"""Hardened localhost-only Starlette server for the CodeAtlas web studio."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import os
import re
import socket
import threading
import webbrowser
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codeatlas.analysis.blast_radius import compute_blast_radius, trace_request_to_db
from codeatlas.models.analysis import AnalysisResult
from codeatlas.models.config import ExportFormat, ScanConfig
from codeatlas.scanner.parser_registry import detect_language
from codeatlas.server.watcher import EventBus, RepoWatcher
from codeatlas.ui import UI_CONTENT_TYPES, read_ui_asset

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420
MAX_PREVIEW_BYTES = 262_144  # 256 KB
_BINARY_SNIFF_BYTES = 8192

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)

_STATIC_UI_ASSETS: frozenset[str] = frozenset(
    {"styles.css", "layouts.js", "canvas_engine.js", "app.js"}
)

_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="#38bdf8" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<rect width="24" height="24" rx="5" fill="#060913" stroke="none"/>'
    '<polygon points="12 3 20 7.5 12 12 4 7.5 12 3"/>'
    '<polyline points="4 12 12 16.5 20 12"/>'
    '<polyline points="4 16.5 12 21 20 16.5"/>'
    "</svg>"
)

_EXTRA_LANGUAGE_BY_EXT: dict[str, str] = {
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".sql": "sql",
    ".sh": "shell",
    ".ps1": "powershell",
    ".xml": "xml",
    ".txt": "text",
}

_EXPORT_MEDIA_TYPES: dict[ExportFormat, str] = {
    ExportFormat.JSON: "application/json; charset=utf-8",
    ExportFormat.MERMAID: "text/plain; charset=utf-8",
    ExportFormat.MARKDOWN: "text/markdown; charset=utf-8",
    ExportFormat.LLMS: "text/plain; charset=utf-8",
    ExportFormat.HTML: "text/html; charset=utf-8",
}

_EXPORT_FORMATS: dict[str, ExportFormat] = {
    "json": ExportFormat.JSON,
    "mermaid": ExportFormat.MERMAID,
    "markdown": ExportFormat.MARKDOWN,
    "llms": ExportFormat.LLMS,
    "html": ExportFormat.HTML,
}

_LOOPBACK_HOSTS: dict[str, str] = {
    "127.0.0.1": "127.0.0.1",
    "localhost": "localhost",
    "::1": "::1",
}


class _FileAccessError(Exception):
    """Structured HTTP error raised when validating `/api/file` requests."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_loopback_host(host: str) -> str:
    """Validate that ``host`` is strictly a local loopback address."""
    cleaned = host.strip().lower()
    canonical = _LOOPBACK_HOSTS.get(cleaned)
    if canonical is None:
        raise ValueError(
            "CodeAtlas server must bind strictly to a loopback host "
            f"('127.0.0.1', 'localhost', or '::1'), got {host!r}."
        )
    return canonical


def _format_base_url(host: str, port: int) -> str:
    validated = validate_loopback_host(host)
    host_part = "[::1]" if validated == "::1" else validated
    return f"http://{host_part}:{port}"


def _build_allowed_hosts_and_origins(
    port: int,
    allowed_hosts: Sequence[str] | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    hosts: set[str] = {
        f"127.0.0.1:{port}",
        f"localhost:{port}",
        f"[::1]:{port}",
    }
    if port == 80:
        hosts.update({"127.0.0.1", "localhost", "[::1]"})
    if allowed_hosts:
        for extra in allowed_hosts:
            cleaned = extra.strip().lower()
            if cleaned:
                hosts.add(cleaned)
    origins = {f"http://{host}" for host in hosts}
    return frozenset(hosts), frozenset(origins)


def _bind_loopback_socket(
    host: str,
    port: int,
    logger: logging.Logger,
) -> socket.socket | None:
    """Bind a loopback TCP socket without ``SO_REUSEADDR`` on Windows.

    Falls back to ephemeral port ``0`` when the requested non-zero port is busy.
    """
    validated_host = validate_loopback_host(host)
    if port < 0 or port > 65535:
        raise ValueError(f"Port must be between 0 and 65535, got {port}")

    family = socket.AF_INET6 if validated_host == "::1" else socket.AF_INET
    bind_addr: tuple[Any, ...] = (
        ("::1", port, 0, 0) if family == socket.AF_INET6 else (validated_host, port)
    )

    def _try_bind(target_addr: tuple[Any, ...]) -> socket.socket:
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            # Never enable SO_REUSEADDR on Windows (os.name == "nt") to prevent port hijacking.
            if os.name == "nt":
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(target_addr)
            sock.listen(128)
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        return sock

    try:
        return _try_bind(bind_addr)
    except OSError as exc:
        if port == 0:
            logger.error("Failed to bind ephemeral loopback port on %s: %s", validated_host, exc)
            return None
        logger.warning(
            "Requested port %d on %s is busy (%s); falling back to an ephemeral port.",
            port,
            validated_host,
            exc,
        )
        ephemeral_addr: tuple[Any, ...] = (
            ("::1", 0, 0, 0) if family == socket.AF_INET6 else (validated_host, 0)
        )
        try:
            return _try_bind(ephemeral_addr)
        except OSError as fallback_exc:
            logger.error(
                "Failed to bind fallback ephemeral loopback port on %s: %s",
                validated_host,
                fallback_exc,
            )
            return None


def _apply_security_headers(headers: MutableHeaders) -> None:
    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "no-referrer"
    headers["X-Frame-Options"] = "DENY"


def _extract_header_values(raw_headers: list[tuple[bytes, bytes]], target_name: bytes) -> list[str]:
    values: list[str] = []
    for name, value in raw_headers:
        if name.lower() == target_name:
            values.append(value.decode("latin-1").strip())
    return values


class _SecurityMiddleware:
    """Pure ASGI middleware enforcing strict Host/Origin/Fetch guards and CSP headers."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
    ) -> None:
        self.app = app
        self._allowed_hosts = allowed_hosts
        self._allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                _apply_security_headers(headers)
            await send(message)

        rejection = self._check_request_guards(scope)
        if rejection is not None:
            response = JSONResponse({"detail": rejection}, status_code=403)
            await response(scope, receive, send_with_headers)
            return

        await self.app(scope, receive, send_with_headers)

    def _check_request_guards(self, scope: Scope) -> str | None:
        raw_headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))

        host_values = _extract_header_values(raw_headers, b"host")
        if len(host_values) != 1 or host_values[0].lower() not in self._allowed_hosts:
            return "Forbidden Host header."

        path = str(scope.get("path", ""))
        if path == "/api" or path.startswith("/api/"):
            fetch_sites = _extract_header_values(raw_headers, b"sec-fetch-site")
            for site in fetch_sites:
                if site.lower() == "cross-site":
                    return "Cross-site API requests are forbidden."

        method = str(scope.get("method", "GET")).upper()
        if method in _MUTATING_METHODS:
            origin_values = _extract_header_values(raw_headers, b"origin")
            if len(origin_values) != 1 or origin_values[0].lower() not in self._allowed_origins:
                return "Missing or forbidden Origin header for mutating request."

        return None


def _is_sensitive_segment(segment: str) -> bool:
    """Return True if a single path segment matches sensitive credential/repo patterns."""
    lower = segment.lower()
    if lower in {".git", ".ssh"}:
        return True
    if lower == ".env" or lower.startswith(".env.") or lower.endswith(".env"):
        return True
    if lower.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    if lower.startswith(("id_rsa", "id_ed25519")):
        return True
    return "secret" in lower or "credentials" in lower


def _has_sensitive_segment(parts: Sequence[str]) -> bool:
    return any(_is_sensitive_segment(part) for part in parts if part and part != ".")


def _resolve_safe_repo_file(repo_root: Path, rel_path: str) -> tuple[Path, str, bytes]:
    """Validate containment, sensitive-file rules, size, and binary sniff for `/api/file`."""
    if "\x00" in rel_path:
        raise _FileAccessError(403, "Path contains NUL bytes.")

    cleaned = rel_path.strip()
    if not cleaned:
        raise _FileAccessError(400, "Missing 'path' query parameter.")

    posix_candidate = cleaned.replace("\\", "/")
    if (
        posix_candidate.startswith("/")
        or PurePosixPath(posix_candidate).is_absolute()
        or PureWindowsPath(cleaned).is_absolute()
        or Path(cleaned).is_absolute()
        or (
            len(posix_candidate) >= 2 and posix_candidate[1] == ":" and posix_candidate[0].isalpha()
        )
    ):
        raise _FileAccessError(403, "Non-relative paths are forbidden.")

    raw_parts = [part for part in posix_candidate.split("/") if part and part != "."]
    if not raw_parts or ".." in raw_parts:
        raise _FileAccessError(403, "Path traversal is forbidden.")

    if _has_sensitive_segment(raw_parts):
        raise _FileAccessError(403, "Access to sensitive files is forbidden.")

    repo_root_resolved = repo_root.resolve()
    candidate = repo_root.joinpath(*raw_parts)

    if candidate.is_symlink():
        try:
            link_target = Path(os.readlink(candidate))
            target_abs = (
                link_target if link_target.is_absolute() else (candidate.parent / link_target)
            ).resolve(strict=False)
        except OSError as exc:
            raise _FileAccessError(403, "Unreadable symlink target.") from exc
        if not target_abs.is_relative_to(repo_root_resolved):
            raise _FileAccessError(403, "Symlink escapes repository root.")

    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise _FileAccessError(403, "Unable to resolve requested path safely.") from exc

    if not resolved.is_relative_to(repo_root_resolved):
        raise _FileAccessError(403, "Path escapes repository root.")

    rel_resolved = resolved.relative_to(repo_root_resolved)
    if _has_sensitive_segment(rel_resolved.parts):
        raise _FileAccessError(403, "Access to sensitive files is forbidden.")

    try:
        if not resolved.exists() or not resolved.is_file():
            raise _FileAccessError(404, "Requested file was not found.")
        file_size = resolved.stat().st_size
    except OSError as exc:
        raise _FileAccessError(404, "Requested file was not found.") from exc

    if file_size > MAX_PREVIEW_BYTES:
        raise _FileAccessError(
            413,
            f"File exceeds {MAX_PREVIEW_BYTES} byte preview limit ({file_size} bytes).",
        )

    try:
        raw_bytes = resolved.read_bytes()
    except OSError as exc:
        raise _FileAccessError(404, "Unable to read requested file.") from exc

    if b"\x00" in raw_bytes[:_BINARY_SNIFF_BYTES]:
        raise _FileAccessError(415, "Binary files cannot be previewed.")

    return resolved, rel_resolved.as_posix(), raw_bytes


def _detect_preview_language(rel_posix: str) -> str:
    lang = detect_language(rel_posix)
    if lang is not None:
        return lang.value
    ext = Path(rel_posix).suffix.lower()
    return _EXTRA_LANGUAGE_BY_EXT.get(ext, "text")


def _export_filename(repo_name: str, fmt: ExportFormat) -> str:
    slug = (
        re.sub(r"[^A-Za-z0-9_-]+", "-", repo_name or "codeatlas").strip("-").lower() or "codeatlas"
    )
    if fmt == ExportFormat.JSON:
        return f"{slug}-architecture.json"
    if fmt == ExportFormat.MERMAID:
        return f"{slug}-architecture.mmd"
    if fmt == ExportFormat.MARKDOWN:
        return "ARCHITECTURE.md"
    if fmt == ExportFormat.LLMS:
        return "llms.txt"
    return f"{slug}-atlas.html"


def _format_sse_event(event: dict[str, Any], *, event_id: int) -> bytes:
    event_name = str(event.get("type", "message")).replace("\r", "").replace("\n", "")
    payload = json.dumps(event, separators=(",", ":"))
    frame = f"id: {event_id}\nevent: {event_name}\ndata: {payload}\n\n"
    return frame.encode("utf-8")


async def _sse_event_stream(
    event_bus: EventBus,
    shutdown_event: asyncio.Event,
    *,
    watch_enabled: bool,
    keepalive_interval: float,
) -> AsyncIterator[bytes]:
    queue = event_bus.subscribe()
    seq = 0
    try:
        seq += 1
        yield _format_sse_event(
            {"type": "watch_status", "watch_enabled": watch_enabled},
            event_id=seq,
        )

        while not shutdown_event.is_set() and not event_bus.is_closed:
            get_task = asyncio.create_task(queue.get())
            stop_task = asyncio.create_task(shutdown_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {get_task, stop_task},
                    timeout=keepalive_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                get_task.cancel()
                stop_task.cancel()
                raise

            for pending_task in pending:
                pending_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending_task

            if stop_task in done or shutdown_event.is_set():
                break

            if get_task in done:
                event = get_task.result()
                if event is None:
                    break
                seq += 1
                yield _format_sse_event(event, event_id=seq)
            else:
                yield b": keepalive\n\n"
    finally:
        event_bus.unsubscribe(queue)


def _default_scan_repository(root_path: Path, config: ScanConfig) -> AnalysisResult:
    from codeatlas.analysis.engine import scan_repository

    return scan_repository(root_path, config=config)


def _default_export_analysis(result: AnalysisResult, fmt: ExportFormat, max_tokens: int) -> str:
    from codeatlas.exporters import export_analysis

    return export_analysis(result, fmt, max_tokens=max_tokens)


def _build_graph_payload(result: AnalysisResult, *, watch_enabled: bool) -> dict[str, Any]:
    return {
        "graph": result.graph.model_dump(mode="json"),
        "health": result.health.model_dump(mode="json"),
        "watch_enabled": watch_enabled,
        "repo_name": result.graph.repo_name,
    }


def create_app(
    repo_path: str | Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    watch: bool = True,
    config: ScanConfig | None = None,
    initial_result: AnalysisResult | None = None,
    allowed_hosts: Sequence[str] | None = None,
    poll_interval: float = 0.8,
    keepalive_interval: float = 15.0,
    scan_fn: Callable[[Path, ScanConfig], AnalysisResult] | None = None,
    export_fn: Callable[[AnalysisResult, ExportFormat, int], str] | None = None,
) -> Starlette:
    """Create the hardened CodeAtlas ASGI application."""
    validate_loopback_host(host)
    if port < 1 or port > 65535:
        raise ValueError(f"create_app port must be between 1 and 65535, got {port}")

    root_path = Path(repo_path).resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"Repository path does not exist: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"Repository path is not a directory: {root_path}")

    scan_config = config or ScanConfig()
    active_scan_fn = scan_fn or _default_scan_repository
    active_export_fn = export_fn or _default_export_analysis

    baseline_result = (
        initial_result if initial_result is not None else active_scan_fn(root_path, scan_config)
    )

    shutdown_event = asyncio.Event()
    event_bus = EventBus(shutdown_event=shutdown_event)
    scan_lock = asyncio.Lock()

    allowed_host_set, allowed_origin_set = _build_allowed_hosts_and_origins(
        port,
        allowed_hosts=allowed_hosts,
    )

    async def handle_index(_request: Request) -> Response:
        return Response(
            content=read_ui_asset("index.html"),
            media_type=UI_CONTENT_TYPES["index.html"],
        )

    async def handle_favicon(_request: Request) -> Response:
        return Response(
            content=_FAVICON_SVG,
            media_type="image/svg+xml; charset=utf-8",
        )

    def _serve_static_asset(asset_name: str) -> Response:
        if asset_name not in _STATIC_UI_ASSETS:
            return JSONResponse({"detail": "Static asset not found."}, status_code=404)
        return Response(
            content=read_ui_asset(asset_name),
            media_type=UI_CONTENT_TYPES[asset_name],
        )

    async def handle_static(request: Request) -> Response:
        asset_name = str(request.path_params.get("asset", ""))
        return _serve_static_asset(asset_name)

    def _make_root_asset_handler(asset_name: str) -> Callable[[Request], Any]:
        async def _handler(_request: Request) -> Response:
            return _serve_static_asset(asset_name)

        return _handler

    async def handle_api_graph(request: Request) -> Response:
        result: AnalysisResult = request.app.state.analysis_result
        watch_enabled: bool = request.app.state.watch_enabled
        return JSONResponse(_build_graph_payload(result, watch_enabled=watch_enabled))

    async def handle_api_blast_radius(request: Request) -> Response:
        node_id = request.query_params.get("node", "").strip()
        if not node_id:
            return JSONResponse({"detail": "Missing 'node' query parameter."}, status_code=400)

        depth_raw = request.query_params.get("depth", "5").strip()
        try:
            depth = int(depth_raw)
        except ValueError:
            return JSONResponse({"detail": "Invalid 'depth' parameter."}, status_code=400)
        if depth < 0 or depth > 50:
            return JSONResponse(
                {"detail": "'depth' must be between 0 and 50."},
                status_code=400,
            )

        result: AnalysisResult = request.app.state.analysis_result
        blast = compute_blast_radius(result.graph, node_id, max_depth=depth)
        return JSONResponse(blast.model_dump(mode="json"))

    async def handle_api_trace(request: Request) -> Response:
        node_id = request.query_params.get("node", "").strip()
        if not node_id:
            return JSONResponse({"detail": "Missing 'node' query parameter."}, status_code=400)

        depth_raw = request.query_params.get("depth", "8").strip()
        try:
            depth = int(depth_raw)
        except ValueError:
            return JSONResponse({"detail": "Invalid 'depth' parameter."}, status_code=400)
        if depth < 0 or depth > 50:
            return JSONResponse(
                {"detail": "'depth' must be between 0 and 50."},
                status_code=400,
            )

        result: AnalysisResult = request.app.state.analysis_result
        trace = trace_request_to_db(result.graph, node_id, max_depth=depth)
        return JSONResponse(trace.model_dump(mode="json"))

    async def handle_api_file(request: Request) -> Response:
        rel_path = request.query_params.get("path")
        if rel_path is None:
            return JSONResponse({"detail": "Missing 'path' query parameter."}, status_code=400)

        try:
            _, normalized_rel, raw_bytes = _resolve_safe_repo_file(
                request.app.state.repo_root,
                rel_path,
            )
        except _FileAccessError as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

        text = raw_bytes.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return JSONResponse(
            {
                "path": normalized_rel,
                "language": _detect_preview_language(normalized_rel),
                "lines": lines,
                "line_count": len(lines),
            }
        )

    async def handle_api_export(request: Request) -> Response:
        fmt_raw = request.query_params.get("format", "json").strip().lower()
        fmt = _EXPORT_FORMATS.get(fmt_raw)
        if fmt is None:
            return JSONResponse(
                {"detail": f"Unsupported export format: {fmt_raw!r}."},
                status_code=400,
            )

        max_tokens = 8000
        max_tokens_raw = request.query_params.get("max_tokens")
        if max_tokens_raw is not None:
            try:
                max_tokens = int(max_tokens_raw.strip())
            except ValueError:
                return JSONResponse(
                    {"detail": "Invalid 'max_tokens' query parameter."},
                    status_code=400,
                )
            if max_tokens < 100:
                return JSONResponse(
                    {"detail": "'max_tokens' must be >= 100."},
                    status_code=400,
                )

        result: AnalysisResult = request.app.state.analysis_result
        rendered = await asyncio.to_thread(active_export_fn, result, fmt, max_tokens)
        filename = _export_filename(result.graph.repo_name, fmt)
        return Response(
            content=rendered,
            media_type=_EXPORT_MEDIA_TYPES[fmt],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def handle_api_events(request: Request) -> Response:
        bus: EventBus = request.app.state.event_bus
        stop_ev: asyncio.Event = request.app.state.shutdown_event
        watch_enabled: bool = request.app.state.watch_enabled
        stream = _sse_event_stream(
            bus,
            stop_ev,
            watch_enabled=watch_enabled,
            keepalive_interval=keepalive_interval,
        )
        return StreamingResponse(
            stream,
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def handle_api_rescan(request: Request) -> Response:
        lock: asyncio.Lock = request.app.state.scan_lock
        bus: EventBus = request.app.state.event_bus
        watch_enabled: bool = request.app.state.watch_enabled
        active_watcher: RepoWatcher | None = request.app.state.watcher

        async with lock:
            fresh_result = await asyncio.to_thread(
                active_scan_fn,
                request.app.state.repo_root,
                request.app.state.scan_config,
            )
            request.app.state.analysis_result = fresh_result
            if active_watcher is not None:
                await active_watcher.prime()

        payload = _build_graph_payload(fresh_result, watch_enabled=watch_enabled)
        event: dict[str, Any] = {
            "type": "graph_updated",
            **payload,
            "changed_files": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await bus.publish(event)
        return JSONResponse(payload)

    @asynccontextmanager
    async def lifespan(starlette_app: Starlette) -> AsyncIterator[None]:
        shutdown_event.clear()
        event_bus.reopen()

        async def _apply_watcher_update(result: AnalysisResult, _changed: list[str]) -> None:
            async with scan_lock:
                starlette_app.state.analysis_result = result

        watcher: RepoWatcher | None = None
        if watch:
            watcher = RepoWatcher(
                root_path,
                event_bus,
                scan_config=scan_config,
                poll_interval=poll_interval,
                scan_fn=active_scan_fn,
                on_update=_apply_watcher_update,
                shutdown_event=shutdown_event,
            )
            starlette_app.state.watcher = watcher
            await watcher.start()

        try:
            yield
        finally:
            shutdown_event.set()
            if watcher is not None:
                await watcher.stop()
                starlette_app.state.watcher = None
            event_bus.close()

    routes = [
        Route("/", handle_index, methods=["GET"]),
        Route("/favicon.svg", handle_favicon, methods=["GET"]),
        Route("/static/{asset:path}", handle_static, methods=["GET"]),
        *(
            Route(f"/{asset}", _make_root_asset_handler(asset), methods=["GET"])
            for asset in sorted(_STATIC_UI_ASSETS)
        ),
        Route("/api/graph", handle_api_graph, methods=["GET"]),
        Route("/api/blast-radius", handle_api_blast_radius, methods=["GET"]),
        Route("/api/trace", handle_api_trace, methods=["GET"]),
        Route("/api/file", handle_api_file, methods=["GET"]),
        Route("/api/export", handle_api_export, methods=["GET"]),
        Route("/api/events", handle_api_events, methods=["GET"]),
        Route("/api/rescan", handle_api_rescan, methods=["POST"]),
    ]

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.repo_root = root_path
    app.state.scan_config = scan_config
    app.state.analysis_result = baseline_result
    app.state.event_bus = event_bus
    app.state.shutdown_event = shutdown_event
    app.state.watcher = None
    app.state.watch_enabled = watch
    app.state.scan_lock = scan_lock
    app.state.allowed_hosts = allowed_host_set
    app.state.allowed_origins = allowed_origin_set

    app.add_middleware(
        _SecurityMiddleware,
        allowed_hosts=allowed_host_set,
        allowed_origins=allowed_origin_set,
    )
    return app


class _CodeAtlasUvicornServer(uvicorn.Server):
    """Uvicorn server subclass firing a ready callback once listening begins."""

    def __init__(
        self,
        config: uvicorn.Config,
        *,
        on_started: Callable[[], None] | None = None,
        stop_event: threading.Event | None = None,
        app_shutdown_event: asyncio.Event | None = None,
    ) -> None:
        super().__init__(config)
        self._on_started = on_started
        self._stop_event = stop_event
        self._app_shutdown_event = app_shutdown_event

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and self._on_started is not None:
            self._on_started()

    async def _serve(self, sockets: list[socket.socket] | None = None) -> None:
        stop_task: asyncio.Task[None] | None = None
        if self._stop_event is not None:
            stop_task = asyncio.create_task(self._watch_stop_event())
        try:
            await super()._serve(sockets=sockets)
        finally:
            if stop_task is not None and not stop_task.done():
                stop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stop_task

    async def _watch_stop_event(self) -> None:
        if self._stop_event is None:
            return
        await asyncio.to_thread(self._stop_event.wait)
        if self._app_shutdown_event is not None:
            self._app_shutdown_event.set()
        self.should_exit = True


def serve_studio(
    repo_path: str | Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    watch: bool = True,
    open_browser: bool = True,
    config: ScanConfig | None = None,
    allowed_hosts: Sequence[str] | None = None,
    poll_interval: float = 0.8,
    log_level: str = "warning",
    on_ready: Callable[[str], object] | None = None,
    stop_event: threading.Event | None = None,
) -> str:
    """Bind a loopback socket, launch the hardened studio server, and return its URL."""
    logger = logging.getLogger("codeatlas.server")
    validated_host = validate_loopback_host(host)
    bound_socket = _bind_loopback_socket(validated_host, port, logger)
    if bound_socket is None:
        raise RuntimeError(f"Unable to bind CodeAtlas server on loopback host {validated_host}.")

    bound_port = int(bound_socket.getsockname()[1])
    bound_url = _format_base_url(validated_host, bound_port)

    app = create_app(
        repo_path,
        host=validated_host,
        port=bound_port,
        watch=watch,
        config=config,
        allowed_hosts=allowed_hosts,
        poll_interval=poll_interval,
    )

    ready_tasks: set[asyncio.Task[Any]] = set()

    def _notify_ready() -> None:
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(bound_url)
        if on_ready is not None:
            res = on_ready(bound_url)
            if inspect.iscoroutine(res):
                task = asyncio.create_task(res)
                ready_tasks.add(task)
                task.add_done_callback(ready_tasks.discard)

    uv_config = uvicorn.Config(
        app,
        host=validated_host,
        port=bound_port,
        log_level=log_level,
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )
    server = _CodeAtlasUvicornServer(
        uv_config,
        on_started=_notify_ready,
        stop_event=stop_event,
        app_shutdown_event=app.state.shutdown_event,
    )

    try:
        server.run(sockets=[bound_socket])
    finally:
        with contextlib.suppress(OSError):
            bound_socket.close()

    return bound_url
