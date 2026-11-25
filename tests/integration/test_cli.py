"""Integration tests for the CodeAtlas Typer CLI and ``python -m codeatlas`` entrypoint."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import codeatlas.cli as cli_mod
from codeatlas import __version__
from codeatlas.cli import app
from codeatlas.exporters import estimate_tokens

runner = CliRunner()


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _create_clean_repo(root: Path) -> Path:
    _write_text(
        root / "models" / "user_model.py",
        "class User:\n    def __init__(self, name: str) -> None:\n        self.name = name\n",
    )
    _write_text(
        root / "services" / "user_service.py",
        "from models.user_model import User\n\n"
        "def get_user(name: str) -> User:\n"
        "    return User(name)\n",
    )
    _write_text(
        root / "routes" / "user_routes.py",
        "from services.user_service import get_user\n\n"
        "def handle_user(name: str) -> str:\n"
        "    return get_user(name).name\n"
        "if __name__ == '__main__':\n"
        "    print(handle_user('alice'))\n",
    )
    return root


def _create_cycle_only_repo(root: Path) -> Path:
    _write_text(
        root / "services" / "alpha_service.py",
        "from services.beta_service import beta_fn\n\n"
        "def alpha_fn(flag: bool) -> int:\n"
        "    return 1 if not flag else beta_fn(False)\n",
    )
    _write_text(
        root / "services" / "beta_service.py",
        "from services.alpha_service import alpha_fn\n\n"
        "def beta_fn(flag: bool) -> int:\n"
        "    return 2 if not flag else alpha_fn(False)\n",
    )
    return root


def _create_violation_only_repo(root: Path) -> Path:
    _write_text(
        root / "services" / "helper_service.py",
        "def notify(msg: str) -> str:\n    return msg\n",
    )
    _write_text(
        root / "models" / "order_model.py",
        "from services.helper_service import notify\n\n"
        "class OrderModel:\n"
        "    def save(self) -> str:\n"
        "        return notify('saved')\n",
    )
    return root


def test_cli_help_version_and_module_entrypoint() -> None:
    help_res = runner.invoke(app, ["--help"])
    assert help_res.exit_code == 0
    for command_name in ("scan", "serve", "export", "demo"):
        assert command_name in help_res.stdout

    no_args_res = runner.invoke(app, [])
    assert "Usage:" in no_args_res.output
    assert "scan" in no_args_res.output

    for flag in ("--version", "-V"):
        ver_res = runner.invoke(app, [flag])
        assert ver_res.exit_code == 0
        assert __version__ in ver_res.stdout

    proc = subprocess.run(
        [sys.executable, "-m", "codeatlas", "--version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0
    assert __version__ in proc.stdout


def test_cli_scan_demo_console_and_console_file_output(
    sample_saas_root: Path,
    tmp_path: Path,
) -> None:
    res = runner.invoke(app, ["scan", str(sample_saas_root)])
    assert res.exit_code == 0, res.output

    out = res.stdout
    assert "sample_saas" in out
    assert "Layer Breakdown" in out
    assert "API Route Inventory" in out
    assert "Circular Dependencies" in out
    assert "Layer Violations" in out
    assert "Hotspots" in out
    assert "Dead Files" in out
    assert "Parse Warnings" in out
    assert "billing_service.py" in out
    assert "invoice_service.py" in out
    assert "analytics_service.py" in out
    assert "legacy_exporter.py" in out
    assert "PaymentRecord.php" in out

    console_file = tmp_path / "dashboard.txt"
    file_res = runner.invoke(
        app,
        ["scan", str(sample_saas_root), "--format", "console", "--output", str(console_file)],
    )
    assert file_res.exit_code == 0, file_res.output
    assert console_file.is_file()
    raw_bytes = console_file.read_bytes()
    assert b"\r\n" not in raw_bytes
    file_text = raw_bytes.decode("utf-8")
    assert "sample_saas" in file_text
    assert "Layer Breakdown" in file_text


def test_cli_scan_fail_gates_and_clean_repo(
    sample_saas_root: Path,
    tmp_path: Path,
) -> None:
    res_cycles = runner.invoke(app, ["scan", str(sample_saas_root), "--fail-on-cycles"])
    assert res_cycles.exit_code == 1
    assert "Quality gate failed" in res_cycles.output

    res_violations = runner.invoke(app, ["scan", str(sample_saas_root), "--fail-on-violations"])
    assert res_violations.exit_code == 1
    assert "Quality gate failed" in res_violations.output

    clean_repo = _create_clean_repo(tmp_path / "clean_repo")
    res_clean = runner.invoke(
        app,
        ["scan", str(clean_repo), "--fail-on-cycles", "--fail-on-violations"],
    )
    assert res_clean.exit_code == 0, res_clean.output

    cycle_repo = _create_cycle_only_repo(tmp_path / "cycle_repo")
    assert runner.invoke(app, ["scan", str(cycle_repo), "--fail-on-violations"]).exit_code == 0
    assert runner.invoke(app, ["scan", str(cycle_repo), "--fail-on-cycles"]).exit_code == 1

    viol_repo = _create_violation_only_repo(tmp_path / "viol_repo")
    assert runner.invoke(app, ["scan", str(viol_repo), "--fail-on-cycles"]).exit_code == 0
    assert runner.invoke(app, ["scan", str(viol_repo), "--fail-on-violations"]).exit_code == 1


def test_cli_scan_non_console_formats(sample_saas_root: Path, tmp_path: Path) -> None:
    stdout_json = runner.invoke(app, ["scan", str(sample_saas_root), "--format", "json"])
    assert stdout_json.exit_code == 0, stdout_json.output
    parsed_stdout = json.loads(stdout_json.stdout)
    assert parsed_stdout["graph"]["repo_name"] == "sample_saas"
    assert "health" in parsed_stdout

    json_file = tmp_path / "scan.json"
    res_json_file = runner.invoke(
        app,
        ["scan", str(sample_saas_root), "--format", "json", "--output", str(json_file)],
    )
    assert res_json_file.exit_code == 0, res_json_file.output
    assert json.loads(json_file.read_text(encoding="utf-8")) == parsed_stdout

    llms_file = tmp_path / "llms.txt"
    res_llms = runner.invoke(
        app,
        [
            "scan",
            str(sample_saas_root),
            "--format",
            "llms",
            "--max-tokens",
            "1200",
            "--output",
            str(llms_file),
        ],
    )
    assert res_llms.exit_code == 0, res_llms.output
    llms_text = llms_file.read_text(encoding="utf-8")
    assert llms_text.startswith("# CODEATLAS sample_saas")
    assert estimate_tokens(llms_text) <= 1200

    html_file = tmp_path / "atlas.html"
    res_html = runner.invoke(
        app,
        ["scan", str(sample_saas_root), "--format", "html", "--output", str(html_file)],
    )
    assert res_html.exit_code == 0, res_html.output
    html_bytes = html_file.read_bytes()
    assert b"\r\n" not in html_bytes
    html_text = html_bytes.decode("utf-8")
    assert 'id="codeatlas-bootstrap"' in html_text
    assert 'http-equiv="Content-Security-Policy"' in html_text


def test_cli_export_all_formats_and_rejects_console(
    sample_saas_root: Path,
    tmp_path: Path,
) -> None:
    expectations: list[tuple[str, str, str]] = [
        ("json", "arch.json", '"repo_name": "sample_saas"'),
        ("markdown", "ARCHITECTURE.md", "# Architecture Report: sample_saas"),
        ("mermaid", "arch.mmd", "flowchart LR"),
        ("llms", "llms.txt", "# CODEATLAS sample_saas"),
        ("html", "atlas.html", 'data-theme="paper"'),
    ]

    for fmt_name, filename, expected_marker in expectations:
        out_path = tmp_path / filename
        args = [
            "export",
            str(sample_saas_root),
            "--format",
            fmt_name,
            "--output",
            str(out_path),
        ]
        if fmt_name == "llms":
            args.extend(["--max-tokens", "1800"])
        if fmt_name == "html":
            args.extend(["--theme", "paper"])

        res = runner.invoke(app, args)
        assert res.exit_code == 0, f"{fmt_name} failed: {res.output}"
        assert out_path.is_file()
        raw_bytes = out_path.read_bytes()
        assert b"\r\n" not in raw_bytes, f"CRLF found in {fmt_name} export"
        text = raw_bytes.decode("utf-8")
        assert expected_marker in text
        if fmt_name == "json":
            assert json.loads(text)["graph"]["repo_name"] == "sample_saas"
        if fmt_name == "llms":
            assert estimate_tokens(text) <= 1800

    rejected_path = tmp_path / "should_not_exist.txt"
    bad_res = runner.invoke(
        app,
        [
            "export",
            str(sample_saas_root),
            "--format",
            "console",
            "--output",
            str(rejected_path),
        ],
    )
    assert bad_res.exit_code == 2
    assert not rejected_path.exists()


def test_cli_serve_validation_and_options(
    sample_saas_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for bad_host in ("0.0.0.0", "evil.com"):
        bad_res = runner.invoke(app, ["serve", str(sample_saas_root), "--host", bad_host])
        assert bad_res.exit_code == 2
        assert "loopback" in bad_res.output.lower()

    missing_res = runner.invoke(app, ["serve", str(tmp_path / "does_not_exist")])
    assert missing_res.exit_code == 2

    captured: dict[str, Any] = {}

    def fake_serve_studio(
        repo_path: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = 8770,
        watch: bool = True,
        open_browser: bool = True,
        on_ready: Any = None,
        **kwargs: Any,
    ) -> str:
        captured["repo_path"] = Path(repo_path)
        captured["host"] = host
        captured["port"] = port
        captured["watch"] = watch
        captured["open_browser"] = open_browser
        captured["kwargs"] = kwargs
        url = f"http://{host}:{port}"
        if on_ready is not None:
            on_ready(url)
        return url

    monkeypatch.setattr(cli_mod, "serve_studio", fake_serve_studio)

    default_watch_res = runner.invoke(
        app,
        ["serve", str(sample_saas_root), "--host", "localhost", "--port", "8899", "--no-open"],
    )
    assert default_watch_res.exit_code == 0, default_watch_res.output
    assert captured["repo_path"] == sample_saas_root.resolve()
    assert captured["host"] == "localhost"
    assert captured["port"] == 8899
    assert captured["watch"] is False
    assert captured["open_browser"] is False
    assert "http://localhost:8899" in default_watch_res.stdout

    watch_res = runner.invoke(
        app,
        ["serve", str(sample_saas_root), "--watch", "--no-open"],
    )
    assert watch_res.exit_code == 0, watch_res.output
    assert captured["watch"] is True
    assert captured["open_browser"] is False
    assert "http://127.0.0.1:8770" in watch_res.stdout


def test_cli_demo_locates_sample_saas_and_calls_serve_studio(
    sample_saas_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_serve_studio(
        repo_path: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = 8770,
        watch: bool = True,
        open_browser: bool = True,
        on_ready: Any = None,
        **kwargs: Any,
    ) -> str:
        captured["repo_path"] = Path(repo_path)
        captured["host"] = host
        captured["port"] = port
        captured["watch"] = watch
        captured["open_browser"] = open_browser
        captured["kwargs"] = kwargs
        url = f"http://{host}:{port}"
        if on_ready is not None:
            on_ready(url)
        return url

    monkeypatch.setattr(cli_mod, "serve_studio", fake_serve_studio)
    monkeypatch.chdir(tmp_path)

    res = runner.invoke(app, ["demo", "--port", "9001", "--no-open"])
    assert res.exit_code == 0, res.output
    assert captured["repo_path"] == sample_saas_root.resolve()
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001
    assert captured["watch"] is False
    assert captured["open_browser"] is False
    assert "sample_saas" in res.stdout
    assert "http://127.0.0.1:9001" in res.stdout


def test_cli_serve_live_subprocess_smoke_api_graph(sample_saas_root: Path) -> None:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "codeatlas",
            "serve",
            str(sample_saas_root),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--no-open",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdout is not None

    output_lines: list[str] = []
    url_found: list[str] = []
    ready_event = threading.Event()
    url_pattern = re.compile(r"http://127\.0\.0\.1:\d+")

    def _read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            output_lines.append(line)
            match = url_pattern.search(line)
            if match is not None and not url_found:
                url_found.append(match.group(0))
                ready_event.set()

    reader = threading.Thread(target=_read_stdout, daemon=True)
    reader.start()

    try:
        assert ready_event.wait(timeout=15.0), (
            f"CLI serve did not emit startup URL in time. Output:\n{''.join(output_lines)}"
        )
        live_url = url_found[0]

        last_exc: Exception | None = None
        resp: httpx.Response | None = None
        for _ in range(20):
            try:
                resp = httpx.get(f"{live_url}/api/graph", timeout=2.0)
                if resp.status_code == 200:
                    break
            except httpx.HTTPError as exc:
                last_exc = exc
            time.sleep(0.15)

        assert resp is not None, f"Failed to reach {live_url}/api/graph: {last_exc}"
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["repo_name"] == "sample_saas"
        assert payload["watch_enabled"] is False
        assert len(payload["graph"]["nodes"]) == 22
        assert len(payload["health"]["cycles"]) >= 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
        reader.join(timeout=3.0)
