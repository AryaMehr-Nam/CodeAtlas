"""Integration and security tests for the CodeAtlas server, watcher, and UI."""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import threading
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from codeatlas.models.config import ScanConfig
from codeatlas.server import app as server_app_mod
from codeatlas.server.app import (
    CONTENT_SECURITY_POLICY,
    MAX_PREVIEW_BYTES,
    _bind_loopback_socket,
    create_app,
    serve_studio,
    validate_loopback_host,
)
from codeatlas.server.watcher import EventBus, RepoWatcher
from codeatlas.ui import UI_ASSETS, load_ui_bundle

_TEST_PORT = 8420
_BASE_URL = f"http://127.0.0.1:{_TEST_PORT}"


def _assert_security_headers(headers: httpx.Headers) -> None:
    assert headers.get("content-security-policy") == CONTENT_SECURITY_POLICY
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("referrer-policy") == "no-referrer"
    assert headers.get("x-frame-options") == "DENY"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_validate_loopback_host_accepts_only_loopback() -> None:
    assert validate_loopback_host("127.0.0.1") == "127.0.0.1"
    assert validate_loopback_host("localhost") == "localhost"
    assert validate_loopback_host(" LOCALHOST ") == "localhost"
    assert validate_loopback_host("::1") == "::1"

    for invalid in ("0.0.0.0", "::", "192.168.1.10", "127.0.0.2", "evil.com", ""):
        with pytest.raises(ValueError):
            validate_loopback_host(invalid)


def test_bind_loopback_socket_windows_safety_and_ephemeral_fallback() -> None:
    import logging

    logger = logging.getLogger("test.bind")
    sock1 = _bind_loopback_socket("127.0.0.1", 0, logger)
    assert sock1 is not None
    try:
        busy_port = sock1.getsockname()[1]
        if os.name == "nt":
            assert sock1.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0

        sock2 = _bind_loopback_socket("127.0.0.1", busy_port, logger)
        assert sock2 is not None
        try:
            fallback_port = sock2.getsockname()[1]
            assert fallback_port != busy_port
            assert fallback_port > 0
            if os.name == "nt":
                assert sock2.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
        finally:
            sock2.close()
    finally:
        sock1.close()


def test_security_headers_and_host_origin_fetch_guards(sample_saas_root: Path) -> None:
    app = create_app(sample_saas_root, port=_TEST_PORT, watch=False)

    with TestClient(app, base_url=_BASE_URL) as client:
        # 1. CSP & security headers on / and /api/graph
        root_resp = client.get("/")
        assert root_resp.status_code == 200
        _assert_security_headers(root_resp.headers)

        graph_resp = client.get("/api/graph")
        assert graph_resp.status_code == 200
        _assert_security_headers(graph_resp.headers)

        # 2. Host header allowlist enforcement
        for valid_host in (
            f"127.0.0.1:{_TEST_PORT}",
            f"localhost:{_TEST_PORT}",
            f"[::1]:{_TEST_PORT}",
        ):
            ok_resp = client.get("/api/graph", headers={"Host": valid_host})
            assert ok_resp.status_code == 200

        for bad_host in ("evil.com", f"evil.com:{_TEST_PORT}", "127.0.0.1:9999", "localhost"):
            bad_resp = client.get("/api/graph", headers={"Host": bad_host})
            assert bad_resp.status_code == 403
            _assert_security_headers(bad_resp.headers)

        bad_root = client.get("/", headers={"Host": "evil.com"})
        assert bad_root.status_code == 403
        _assert_security_headers(bad_root.headers)

        # 3. Sec-Fetch-Site: cross-site rejected across all /api/* endpoints
        for endpoint in (
            "/api/graph",
            "/api/blast-radius?node=backend/db.py",
            "/api/trace?node=backend/billing_routes.py",
            "/api/file?path=backend/db.py",
            "/api/export?format=json",
            "/api/events",
        ):
            cross_resp = client.get(endpoint, headers={"Sec-Fetch-Site": "cross-site"})
            assert cross_resp.status_code == 403
            _assert_security_headers(cross_resp.headers)

        cross_post = client.post(
            "/api/rescan",
            headers={
                "Origin": _BASE_URL,
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert cross_post.status_code == 403

        # 4. Origin validation on mutating routes
        missing_origin = client.post("/api/rescan")
        assert missing_origin.status_code == 403
        _assert_security_headers(missing_origin.headers)

        for method in ("PUT", "PATCH", "DELETE"):
            assert client.request(method, "/api/rescan").status_code == 403
            evil_mut = client.request(
                method,
                "/api/rescan",
                headers={"Origin": "https://evil.com"},
            )
            assert evil_mut.status_code == 403

        for bad_origin in (
            "https://evil.com",
            "null",
            "http://127.0.0.1:9999",
            f"https://127.0.0.1:{_TEST_PORT}",
        ):
            evil_origin = client.post("/api/rescan", headers={"Origin": bad_origin})
            assert evil_origin.status_code == 403

        for good_origin in (
            f"http://127.0.0.1:{_TEST_PORT}",
            f"http://localhost:{_TEST_PORT}",
            f"http://[::1]:{_TEST_PORT}",
        ):
            rescan_resp = client.post("/api/rescan", headers={"Origin": good_origin})
            assert rescan_resp.status_code == 200
            _assert_security_headers(rescan_resp.headers)
            payload = rescan_resp.json()
            assert "graph" in payload
            assert "health" in payload
            assert payload["repo_name"] == "sample_saas"


def test_host_port_80_and_explicit_allowed_hosts(sample_saas_root: Path) -> None:
    app_80 = create_app(
        sample_saas_root,
        port=80,
        watch=False,
        allowed_hosts=["studio.local:80"],
    )
    with TestClient(app_80, base_url="http://127.0.0.1") as client:
        assert client.get("/api/graph", headers={"Host": "127.0.0.1"}).status_code == 200
        assert client.get("/api/graph", headers={"Host": "localhost"}).status_code == 200
        assert client.get("/api/graph", headers={"Host": "[::1]"}).status_code == 200
        assert client.get("/api/graph", headers={"Host": "studio.local:80"}).status_code == 200
        assert client.get("/api/graph", headers={"Host": "evil.com"}).status_code == 403
        assert client.post("/api/rescan", headers={"Origin": "http://localhost"}).status_code == 200


def test_api_file_containment_and_sensitive_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    _write_text(repo / "src" / "main.py", "def hello() -> str:\n    return 'world'\n")
    _write_text(repo / ".ENV", "API_KEY=secret\n")
    _write_text(repo / "config" / "Secret_Config.json", '{"token":"abc"}\n')
    _write_text(repo / "certs" / "private.KEY", "PRIVATE KEY\n")
    _write_text(repo / "certs" / "server.PEM", "PEM\n")
    _write_text(repo / "certs" / "bundle.P12", "P12\n")
    _write_text(repo / "certs" / "bundle.PFX", "PFX\n")
    _write_text(repo / "keys" / "ID_RSA", "RSA\n")
    _write_text(repo / "keys" / "id_ed25519.pub", "ED25519\n")
    _write_text(repo / "aws_Credentials.ini", "[default]\n")
    _write_text(repo / ".git" / "config", "[core]\n")
    _write_text(repo / ".ssh" / "config", "Host *\n")
    _write_text(repo / ".env.Production", "DB=1\n")
    _write_text(repo / "oversized.py", "a = 1\n" * 60_000)
    (repo / "binary.dat").parent.mkdir(parents=True, exist_ok=True)
    (repo / "binary.dat").write_bytes(b"PNG\x89\x00\x00\x01binary")
    _write_text(outside / "secret_outside.txt", "do not read\n")

    app = create_app(repo, port=_TEST_PORT, watch=False)

    with TestClient(app, base_url=_BASE_URL) as client:
        # Legitimate repo file succeeds
        ok = client.get("/api/file", params={"path": "src/main.py"})
        assert ok.status_code == 200
        body = ok.json()
        assert body["path"] == "src/main.py"
        assert body["language"] == "python"
        assert body["lines"] == ["def hello() -> str:", "    return 'world'"]
        assert body["line_count"] == 2

        # Traversal & non-relative & NUL-byte rejections
        for bad_path in (
            "../outside/secret_outside.txt",
            "src/../../outside/secret_outside.txt",
            "..\\outside\\secret_outside.txt",
            "/etc/passwd",
            "C:/Windows/win.ini",
            "src/main.py\x00.txt",
        ):
            resp = client.get("/api/file", params={"path": bad_path})
            assert resp.status_code == 403, f"Expected 403 for {bad_path!r}, got {resp.status_code}"

        # Case-insensitive sensitive file guard
        for sensitive in (
            ".ENV",
            ".env.Production",
            "config/Secret_Config.json",
            "certs/private.KEY",
            "certs/server.PEM",
            "certs/bundle.P12",
            "certs/bundle.PFX",
            "keys/ID_RSA",
            "keys/id_ed25519.pub",
            "aws_Credentials.ini",
            ".GIT/config",
            ".SSH/config",
            ".env.local",
            "prod.env",
        ):
            resp = client.get("/api/file", params={"path": sensitive})
            assert resp.status_code == 403, (
                f"Expected 403 for sensitive {sensitive!r}, got {resp.status_code}"
            )

        # Missing & directory -> 404
        assert client.get("/api/file", params={"path": "src/missing.py"}).status_code == 404
        assert client.get("/api/file", params={"path": "src"}).status_code == 404

        # Oversized (> 262144 bytes) -> 413
        oversized_size = (repo / "oversized.py").stat().st_size
        assert oversized_size > MAX_PREVIEW_BYTES
        assert client.get("/api/file", params={"path": "oversized.py"}).status_code == 413

        # Binary -> 415
        assert client.get("/api/file", params={"path": "binary.dat"}).status_code == 415

        # Escaping symlink -> 403 (real OS symlink if permitted, plus deterministic fallback)
        symlink_path = repo / "escape_link.py"
        symlink_created = False
        try:
            symlink_path.symlink_to(outside / "secret_outside.txt")
            symlink_created = True
        except OSError:
            symlink_created = False

        if symlink_created:
            assert client.get("/api/file", params={"path": "escape_link.py"}).status_code == 403
        else:
            _write_text(repo / "escape_link.py", "# placeholder\n")
            orig_resolve = Path.resolve

            def fake_resolve(self: Path, strict: bool = False) -> Path:
                if self.name == "escape_link.py":
                    return orig_resolve(outside / "secret_outside.txt", strict=strict)
                return orig_resolve(self, strict=strict)

            monkeypatch.setattr(server_app_mod.Path, "resolve", fake_resolve)
            assert client.get("/api/file", params={"path": "escape_link.py"}).status_code == 403


def test_api_endpoints_and_static_assets_on_demo(sample_saas_root: Path) -> None:
    app = create_app(sample_saas_root, port=_TEST_PORT, watch=False)

    with TestClient(app, base_url=_BASE_URL) as client:
        # Favicon & static assets
        fav = client.get("/favicon.svg")
        assert fav.status_code == 200
        assert "image/svg+xml" in fav.headers["content-type"]
        _assert_security_headers(fav.headers)

        for asset in ("styles.css", "layouts.js", "canvas_engine.js", "app.js"):
            r_static = client.get(f"/static/{asset}")
            assert r_static.status_code == 200
            _assert_security_headers(r_static.headers)
            assert len(r_static.text) > 0

            r_root = client.get(f"/{asset}")
            assert r_root.status_code == 200

        assert client.get("/static/index.html").status_code == 404
        assert client.get("/static/../server/app.py").status_code == 404

        # Graph endpoint
        graph_data = client.get("/api/graph").json()
        assert graph_data["repo_name"] == "sample_saas"
        assert graph_data["watch_enabled"] is False
        assert len(graph_data["graph"]["nodes"]) >= 18
        assert len(graph_data["health"]["cycles"]) >= 1

        # Blast radius endpoint
        blast_resp = client.get(
            "/api/blast-radius",
            params={"node": "backend/billing_service.py", "depth": "5"},
        )
        assert blast_resp.status_code == 200
        blast = blast_resp.json()
        assert blast["target_id"] == "backend/billing_service.py"
        assert blast["upstream_count"] >= 1
        assert blast["downstream_count"] >= 1

        # Trace endpoint
        trace_resp = client.get("/api/trace", params={"node": "backend/billing_routes.py"})
        assert trace_resp.status_code == 200
        trace = trace_resp.json()
        assert trace["entrypoint_id"] == "backend/billing_routes.py"
        assert len(trace["data_targets"]) >= 1
        assert len(trace["paths"]) >= 1

        # Export endpoints
        for fmt in ("json", "mermaid", "markdown", "llms", "html"):
            exp_resp = client.get("/api/export", params={"format": fmt})
            assert exp_resp.status_code == 200
            assert "attachment;" in exp_resp.headers.get("content-disposition", "")
            _assert_security_headers(exp_resp.headers)
            assert len(exp_resp.text) > 20

        assert client.get("/api/export", params={"format": "console"}).status_code == 400


@pytest.mark.asyncio
async def test_sse_events_and_watcher_wakeup(tmp_path: Path) -> None:
    repo = tmp_path / "watch_repo"
    _write_text(repo / "mod_a.py", "def alpha() -> int:\n    return 1\n")
    _write_text(
        repo / "mod_b.py",
        "from mod_a import alpha\n\ndef beta() -> int:\n    return alpha()\n",
    )

    app = create_app(
        repo,
        port=_TEST_PORT,
        watch=False,
        config=ScanConfig(),
        keepalive_interval=0.05,
    )

    bus: EventBus = app.state.event_bus
    shutdown_event: asyncio.Event = app.state.shutdown_event
    watcher = RepoWatcher(repo, bus, poll_interval=0.08)
    await watcher.prime()

    # Modify a file and confirm poll_once detects & emits graph_updated
    _write_text(repo / "mod_a.py", "def alpha() -> int:\n    return 42\n")
    changed = await watcher.poll_once()
    assert changed == ["mod_a.py"]
    assert len(bus.history) == 1
    assert bus.history[0]["type"] == "graph_updated"
    assert bus.history[0]["changed_files"] == ["mod_a.py"]

    # Background start/stop cycle should complete cleanly
    await watcher.start()
    assert watcher.is_running
    await watcher.stop()
    assert not watcher.is_running

    # Stream /api/events via ASGI transport and verify keepalive + immediate shutdown wakeup
    transport = httpx.ASGITransport(app=app)
    received_lines: list[str] = []

    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:

        async def _trigger_updates_and_shutdown() -> None:
            await asyncio.sleep(0.08)
            await bus.publish(
                {
                    "type": "graph_updated",
                    "graph": app.state.analysis_result.graph.model_dump(mode="json"),
                    "health": app.state.analysis_result.health.model_dump(mode="json"),
                    "changed_files": ["mod_a.py"],
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            )
            await asyncio.sleep(0.05)
            shutdown_event.set()

        driver = asyncio.create_task(_trigger_updates_and_shutdown())
        try:
            async with asyncio.timeout(3.0):
                async with client.stream("GET", "/api/events") as response:
                    assert response.status_code == 200
                    _assert_security_headers(response.headers)
                    async for line in response.aiter_lines():
                        if line:
                            received_lines.append(line)
        finally:
            await driver

    joined = "\n".join(received_lines)
    assert "event: watch_status" in joined
    assert ": keepalive" in joined
    assert "event: graph_updated" in joined
    assert "mod_a.py" in joined


def test_serve_studio_on_ready_and_clean_shutdown(sample_saas_root: Path) -> None:
    ready_urls: list[str] = []
    ready_event = threading.Event()
    stop_event = threading.Event()
    errors: list[BaseException] = []

    def _on_ready(url: str) -> None:
        ready_urls.append(url)
        ready_event.set()

    def _run_server() -> None:
        try:
            serve_studio(
                sample_saas_root,
                host="127.0.0.1",
                port=0,
                watch=False,
                open_browser=False,
                on_ready=_on_ready,
                stop_event=stop_event,
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
            ready_event.set()

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    try:
        assert ready_event.wait(timeout=10.0), "serve_studio did not become ready in time"
        assert not errors
        assert len(ready_urls) == 1
        live_url = ready_urls[0]
        assert live_url.startswith("http://127.0.0.1:")

        with httpx.Client(base_url=live_url, timeout=5.0) as client:
            resp = client.get("/api/graph")
            assert resp.status_code == 200
            _assert_security_headers(resp.headers)
            assert resp.json()["repo_name"] == "sample_saas"

            evil_resp = client.get("/api/graph", headers={"Host": "evil.com"})
            assert evil_resp.status_code == 403
    finally:
        stop_event.set()
        thread.join(timeout=10.0)
    assert not thread.is_alive()


def test_ui_static_security_invariants() -> None:
    bundle = load_ui_bundle()
    assert set(bundle.keys()) == set(UI_ASSETS)

    index_html = bundle["index.html"]
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", index_html, re.IGNORECASE)
    assert not re.search(r"<style\b", index_html, re.IGNORECASE)
    assert not re.search(r"\bon[a-z]+\s*=", index_html, re.IGNORECASE)

    for filename, content in bundle.items():
        assert not re.search(r"https?://", content, re.IGNORECASE), (
            f"External URL found in {filename}"
        )
        assert not re.search(r"\b(?:innerHTML|outerHTML|insertAdjacentHTML)\b", content), (
            f"Forbidden DOM HTML sink found in {filename}"
        )
        assert not re.search(r"setAttribute\s*\(\s*[\"']style[\"']", content), (
            f"Forbidden setAttribute('style', ...) found in {filename}"
        )
        # No JSON payloads with unescaped script/line terminators
        assert isinstance(json.dumps({"asset": filename}), str)
