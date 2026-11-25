"""Unit tests for CodeAtlas exporters (JSON, Mermaid, Markdown, llms.txt, HTML bundle)."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from codeatlas.exporters import (
    estimate_tokens,
    export_analysis,
    export_html_bundle,
    export_json,
    export_llms_txt,
    export_markdown,
    export_mermaid,
    write_export,
)
from codeatlas.models.analysis import (
    AnalysisResult,
    ArchSmell,
    Cycle,
    HealthBreakdown,
    HealthReport,
    Severity,
    SmellKind,
)
from codeatlas.models.config import ExportFormat, Language, Layer, ThemeMode
from codeatlas.models.graph import (
    Cluster,
    CodeGraph,
    Edge,
    Node,
    ParseWarning,
    RouteInfo,
    Symbol,
    SymbolKind,
)


def _make_sample_result(*, reverse_order: bool = False) -> AnalysisResult:
    nodes = [
        Node(
            id="frontend/DashboardPage.tsx",
            path="frontend/DashboardPage.tsx",
            language=Language.TYPESCRIPT,
            layer=Layer.FRONTEND,
            cluster="frontend",
            loc=85,
            code_lines=70,
            comment_lines=5,
            complexity=6,
            fan_in=0,
            fan_out=1,
            instability=1.0,
            symbols=[
                Symbol(
                    name="DashboardPage",
                    kind=SymbolKind.COMPONENT,
                    line=10,
                    end_line=80,
                    exported=True,
                )
            ],
        ),
        Node(
            id="backend/billing_routes.py",
            path="backend/billing_routes.py",
            language=Language.PYTHON,
            layer=Layer.ENTRYPOINT,
            cluster="backend",
            loc=64,
            code_lines=52,
            comment_lines=4,
            complexity=4,
            fan_in=1,
            fan_out=2,
            instability=0.6667,
            is_entrypoint=True,
            routes=[
                RouteInfo(
                    method="POST",
                    path="/api/billing/charge",
                    handler="charge_subscription",
                    line=18,
                    framework="fastapi",
                ),
                RouteInfo(
                    method="GET",
                    path="/api/billing/status",
                    handler="get_billing_status",
                    line=34,
                    framework="fastapi",
                ),
            ],
            symbols=[
                Symbol(
                    name="charge_subscription",
                    kind=SymbolKind.FUNCTION,
                    line=18,
                    end_line=32,
                    exported=True,
                )
            ],
        ),
        Node(
            id="backend/billing_service.py",
            path="backend/billing_service.py",
            language=Language.PYTHON,
            layer=Layer.SERVICE,
            cluster="backend",
            loc=380,
            code_lines=320,
            comment_lines=25,
            complexity=31,
            fan_in=2,
            fan_out=3,
            instability=0.6,
            health_status="critical",
            symbols=[
                Symbol(
                    name="process_charge",
                    kind=SymbolKind.FUNCTION,
                    line=12,
                    end_line=120,
                    exported=True,
                ),
                Symbol(
                    name="calculate_proration",
                    kind=SymbolKind.FUNCTION,
                    line=125,
                    end_line=210,
                    exported=True,
                ),
            ],
        ),
        Node(
            id="backend/invoice_service.py",
            path="backend/invoice_service.py",
            language=Language.PYTHON,
            layer=Layer.SERVICE,
            cluster="backend",
            loc=140,
            code_lines=115,
            comment_lines=10,
            complexity=11,
            fan_in=2,
            fan_out=2,
            instability=0.5,
            health_status="critical",
            symbols=[
                Symbol(
                    name="create_invoice",
                    kind=SymbolKind.FUNCTION,
                    line=15,
                    end_line=70,
                    exported=True,
                )
            ],
        ),
        Node(
            id="backend/payment_model.py",
            path="backend/payment_model.py",
            language=Language.PYTHON,
            layer=Layer.DATA,
            cluster="backend",
            loc=75,
            code_lines=60,
            comment_lines=6,
            complexity=5,
            fan_in=2,
            fan_out=1,
            instability=0.3333,
            has_db_access=True,
            health_status="critical",
            symbols=[
                Symbol(
                    name="PaymentRecord",
                    kind=SymbolKind.CLASS,
                    line=8,
                    end_line=68,
                    exported=True,
                )
            ],
        ),
        Node(
            id="backend/crypto.py",
            path="backend/crypto.py",
            language=Language.PYTHON,
            layer=Layer.SHARED,
            cluster="backend",
            loc=40,
            code_lines=34,
            comment_lines=2,
            complexity=3,
            fan_in=1,
            fan_out=0,
            instability=0.0,
            symbols=[
                Symbol(
                    name="sign_payload",
                    kind=SymbolKind.FUNCTION,
                    line=5,
                    end_line=28,
                    exported=True,
                )
            ],
        ),
        Node(
            id="backend/legacy_exporter.py",
            path="backend/legacy_exporter.py",
            language=Language.PYTHON,
            layer=Layer.SHARED,
            cluster="backend",
            loc=48,
            code_lines=40,
            comment_lines=3,
            complexity=4,
            fan_in=0,
            fan_out=0,
            instability=0.0,
            health_status="warning",
            symbols=[
                Symbol(
                    name="export_csv_legacy",
                    kind=SymbolKind.FUNCTION,
                    line=4,
                    end_line=36,
                    exported=True,
                )
            ],
        ),
        Node(
            id="tests/test_billing.py",
            path="tests/test_billing.py",
            language=Language.PYTHON,
            layer=Layer.TEST,
            cluster="tests",
            loc=55,
            code_lines=48,
            comment_lines=2,
            complexity=2,
            fan_in=0,
            fan_out=1,
            instability=1.0,
        ),
    ]

    edges = [
        Edge(
            source="frontend/DashboardPage.tsx",
            target="backend/billing_routes.py",
            symbols=["charge_subscription"],
        ),
        Edge(
            source="backend/billing_routes.py",
            target="backend/billing_service.py",
            symbols=["process_charge"],
        ),
        Edge(
            source="backend/billing_routes.py",
            target="backend/invoice_service.py",
            symbols=["create_invoice"],
        ),
        Edge(
            source="backend/billing_service.py",
            target="backend/invoice_service.py",
            symbols=["create_invoice"],
            is_cycle=True,
        ),
        Edge(
            source="backend/invoice_service.py",
            target="backend/billing_service.py",
            symbols=["calculate_proration"],
            is_cycle=True,
        ),
        Edge(
            source="backend/billing_service.py",
            target="backend/payment_model.py",
            symbols=["PaymentRecord"],
        ),
        Edge(
            source="backend/billing_service.py",
            target="backend/crypto.py",
            symbols=["sign_payload"],
        ),
        Edge(
            source="backend/payment_model.py",
            target="backend/invoice_service.py",
            symbols=["create_invoice"],
            is_violation=True,
        ),
        Edge(
            source="tests/test_billing.py",
            target="backend/billing_service.py",
            symbols=["process_charge"],
        ),
    ]

    if reverse_order:
        nodes = list(reversed(nodes))
        edges = list(reversed(edges))

    clusters = [
        Cluster(
            id="backend",
            label="backend",
            directory="backend",
            node_ids=[
                "backend/billing_routes.py",
                "backend/billing_service.py",
                "backend/crypto.py",
                "backend/invoice_service.py",
                "backend/legacy_exporter.py",
                "backend/payment_model.py",
            ],
            primary_layer=Layer.SERVICE,
        ),
        Cluster(
            id="frontend",
            label="frontend",
            directory="frontend",
            node_ids=["frontend/DashboardPage.tsx"],
            primary_layer=Layer.FRONTEND,
        ),
        Cluster(
            id="tests",
            label="tests",
            directory="tests",
            node_ids=["tests/test_billing.py"],
            primary_layer=Layer.TEST,
        ),
    ]

    cycle = Cycle(
        id="cycle-001",
        nodes=["backend/billing_service.py", "backend/invoice_service.py"],
        edges=[
            ("backend/billing_service.py", "backend/invoice_service.py"),
            ("backend/invoice_service.py", "backend/billing_service.py"),
        ],
        display_path=(
            "backend/billing_service.py -> backend/invoice_service.py -> backend/billing_service.py"
        ),
    )

    smells = [
        ArchSmell(
            rule_id="ARCH-CYC-001",
            kind=SmellKind.CIRCULAR_DEPENDENCY,
            severity=Severity.HIGH,
            title="Circular dependency (2 modules)",
            description=f"Cycle detected: {cycle.display_path}",
            nodes=list(cycle.nodes),
            edges=list(cycle.edges),
            remediation="Extract shared billing primitives.",
        ),
        ArchSmell(
            rule_id="ARCH-LAY-001",
            kind=SmellKind.LAYER_VIOLATION,
            severity=Severity.HIGH,
            title="Layer violation: data -> service",
            description=(
                "backend/payment_model.py (data) depends on backend/invoice_service.py (service)."
            ),
            nodes=["backend/payment_model.py", "backend/invoice_service.py"],
            edges=[("backend/payment_model.py", "backend/invoice_service.py")],
            remediation="Move service orchestration out of data model.",
        ),
        ArchSmell(
            rule_id="ARCH-GOD-001",
            kind=SmellKind.GOD_MODULE,
            severity=Severity.MEDIUM,
            title="God module hotspot: billing_service.py",
            description="backend/billing_service.py exceeds LOC and complexity thresholds.",
            nodes=["backend/billing_service.py"],
            remediation="Split billing workflow responsibilities.",
        ),
        ArchSmell(
            rule_id="ARCH-DEAD-001",
            kind=SmellKind.DEAD_CODE,
            severity=Severity.LOW,
            title="Potentially dead module: legacy_exporter.py",
            description="backend/legacy_exporter.py has fan_in=0 and no routes.",
            nodes=["backend/legacy_exporter.py"],
            remediation="Remove obsolete legacy exporter.",
        ),
    ]

    graph = CodeGraph(
        repo_name="synthetic-saas",
        root_path="/workspace/synthetic-saas",
        truncated=False,
        total_files_seen=len(nodes),
        nodes=nodes,
        edges=edges,
        clusters=clusters,
        warnings=[
            ParseWarning(
                path="backend/legacy_exporter.py",
                message="Minor fallback note",
                fallback_used="stdlib-ast",
            )
        ],
    )
    health = HealthReport(
        score=71.5,
        grade="C",
        layer_counts={
            Layer.FRONTEND: 1,
            Layer.ENTRYPOINT: 1,
            Layer.SERVICE: 2,
            Layer.DATA: 1,
            Layer.SHARED: 2,
            Layer.TEST: 1,
        },
        cycles=[cycle],
        smells=smells,
        hotspots=["backend/billing_service.py"],
        dead_files=["backend/legacy_exporter.py"],
        breakdown=HealthBreakdown(
            base_score=100.0,
            cycle_penalty=10.0,
            violation_penalty=10.0,
            god_penalty=5.0,
            dead_penalty=3.5,
        ),
    )
    return AnalysisResult(graph=graph, health=health)


def _make_clean_result() -> AnalysisResult:
    node = Node(
        id="app/main.py",
        path="app/main.py",
        language=Language.PYTHON,
        layer=Layer.ENTRYPOINT,
        loc=24,
        code_lines=20,
        complexity=2,
        is_entrypoint=True,
    )
    return AnalysisResult(
        graph=CodeGraph(
            repo_name="clean-app",
            root_path="/workspace/clean-app",
            total_files_seen=1,
            nodes=[node],
            edges=[],
        ),
        health=HealthReport(
            score=100.0,
            grade="A+",
            layer_counts={Layer.ENTRYPOINT: 1},
        ),
    )


class _HtmlBundleInspector(HTMLParser):
    """Collect HTML metadata, inline style/script blocks, and links for CSP verification."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.html_attrs: dict[str, str | None] = {}
        self.meta_csp: str | None = None
        self.links: list[dict[str, str | None]] = []
        self.styles: list[str] = []
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self._capture_tag: str | None = None
        self._capture_attrs: dict[str, str | None] = {}
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): v for k, v in attrs}
        tag_lower = tag.lower()
        if tag_lower == "html":
            self.html_attrs = attr_map
        elif tag_lower == "meta":
            if (attr_map.get("http-equiv") or "").lower() == "content-security-policy":
                self.meta_csp = attr_map.get("content")
        elif tag_lower == "link":
            self.links.append(attr_map)
        elif tag_lower in {"style", "script"}:
            self._capture_tag = tag_lower
            self._capture_attrs = attr_map
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture_tag is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._capture_tag == tag_lower:
            body = "".join(self._buf)
            if tag_lower == "style":
                self.styles.append(body)
            else:
                self.scripts.append((self._capture_attrs, body))
            self._capture_tag = None
            self._capture_attrs = {}
            self._buf = []


def _sha256_token(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def test_export_json_deterministic_sorted_indented() -> None:
    result = _make_sample_result()
    output = export_json(result)
    assert output == export_analysis(result, ExportFormat.JSON)
    assert output == export_json(result)
    assert "\r\n" not in output

    parsed = json.loads(output)
    assert parsed["graph"]["repo_name"] == "synthetic-saas"
    assert parsed["health"]["grade"] == "C"
    assert output == json.dumps(parsed, indent=2, sort_keys=True) + "\n"


def test_export_mermaid_subgraphs_sanitized_ids_and_highlights() -> None:
    result_a = _make_sample_result(reverse_order=False)
    result_b = _make_sample_result(reverse_order=True)

    mermaid_a = export_mermaid(result_a)
    mermaid_b = export_mermaid(result_b)
    assert mermaid_a == mermaid_b
    assert mermaid_a == export_analysis(result_a, ExportFormat.MERMAID)

    lines = mermaid_a.splitlines()
    assert lines[0] == "flowchart LR"
    assert 'subgraph layer_frontend["Frontend"]' in mermaid_a
    assert 'subgraph layer_entrypoint["Entrypoint"]' in mermaid_a
    assert 'subgraph layer_service["Service"]' in mermaid_a
    assert 'subgraph layer_data["Data"]' in mermaid_a
    assert 'subgraph layer_shared["Shared"]' in mermaid_a
    assert 'subgraph layer_test["Test"]' in mermaid_a

    # Cycle and violation dashed arrows + crimson linkStyle directives
    assert "-. cycle .->" in mermaid_a
    assert "-. violation .->" in mermaid_a
    assert "linkStyle " in mermaid_a
    assert "stroke-dasharray:5 5;" in mermaid_a
    assert "#e11d48" in mermaid_a
    assert "#f43f5e" in mermaid_a


def test_export_mermaid_disambiguates_colliding_sanitized_ids() -> None:
    nodes = [
        Node(id="pkg/a_b.py", path="pkg/a_b.py", language=Language.PYTHON, layer=Layer.SHARED),
        Node(id="pkg/a/b.py", path="pkg/a/b.py", language=Language.PYTHON, layer=Layer.SHARED),
    ]
    edge = Edge(source="pkg/a_b.py", target="pkg/a/b.py")
    result = AnalysisResult(
        graph=CodeGraph(repo_name="collision", root_path="/repo", nodes=nodes, edges=[edge]),
        health=HealthReport(score=100.0, grade="A+"),
    )
    mermaid = export_mermaid(result)
    assert "n_pkg_a_b_py[" in mermaid
    assert "n_pkg_a_b_py_2[" in mermaid
    assert "n_pkg_a_b_py_2 --> n_pkg_a_b_py" in mermaid


def test_export_markdown_sections_and_clean_fallback() -> None:
    result = _make_sample_result()
    md = export_markdown(result)
    assert md == export_analysis(result, ExportFormat.MARKDOWN)
    assert md == export_markdown(_make_sample_result(reverse_order=True))

    assert "# Architecture Report: synthetic-saas" in md
    assert "## Health Score Summary" in md
    assert "**71.5 / 100**" in md
    assert "## Layer Breakdown" in md
    assert "## Dependency Graph" in md
    assert "```mermaid\nflowchart LR" in md
    assert "## API Route Inventory" in md
    assert "`POST` | `/api/billing/charge`" in md
    assert "## Hotspots" in md
    assert "backend/billing_service.py" in md
    assert "## Circular Dependencies" in md
    assert "**cycle-001**" in md
    assert "## Layer Violations" in md
    assert "**[HIGH] Layer violation: data -> service**" in md
    assert "## Dead-Code Candidates" in md
    assert "`backend/legacy_exporter.py`" in md

    clean_md = export_markdown(_make_clean_result())
    assert "_No HTTP or API routes detected._" in clean_md
    assert "_No God module hotspots detected._" in clean_md
    assert "_No circular dependencies detected._" in clean_md
    assert "_No architectural layer violations detected._" in clean_md
    assert "_No dead-code candidates detected._" in clean_md


def test_export_llms_txt_prioritization_and_strict_token_budget() -> None:
    result = _make_sample_result()
    full = export_llms_txt(result, max_tokens=8000)
    assert full == export_analysis(result, ExportFormat.LLMS, max_tokens=8000)
    assert estimate_tokens(full) <= 8000
    assert "# CODEATLAS synthetic-saas" in full
    assert "## LAYERS" in full
    assert "## STRUCTURAL SMELLS" in full
    assert "## ENTRYPOINTS & ROUTES" in full
    assert "## CENTRAL HUBS" in full
    assert "## MODULES" in full

    # Highest degree hub (billing_service.py: in=2, out=3 => deg=5) must appear first in hubs
    hub_lines = [line for line in full.splitlines() if line.startswith("HUB ")]
    assert hub_lines
    assert hub_lines[0].startswith("HUB backend/billing_service.py ")
    assert "exports: process_charge, calculate_proration" in full

    # Strict budget adherence across tight and tiny caps
    for budget in (300, 150, 60, 20):
        bounded = export_llms_txt(result, max_tokens=budget)
        assert len(bounded) / 4.0 <= budget
        assert math.ceil(len(bounded) / 4.0) <= budget
        assert bounded.startswith("# CODEATLAS")

    # Tight budget prioritizes summary/smells over remaining compact modules
    tight = export_llms_txt(result, max_tokens=140)
    assert "HEALTH: 71.5/100 (C)" in tight
    assert "## STRUCTURAL SMELLS" in tight
    assert "## MODULES" not in tight

    with pytest.raises(ValueError, match="max_tokens"):
        export_llms_txt(result, max_tokens=0)


def test_export_html_bundle_csp_hashes_bootstrap_and_write_export(tmp_path: Path) -> None:
    result = _make_sample_result()
    # Inject dangerous payloads to ensure JSON bootstrap escaping works properly.
    result.graph.nodes[0].symbols.append(
        Symbol(
            name="danger</script>\u2028\u2029Tag",
            kind=SymbolKind.VARIABLE,
            line=2,
            end_line=2,
            exported=True,
        )
    )

    html_obsidian = export_html_bundle(result)
    assert html_obsidian == export_analysis(result, ExportFormat.HTML)
    assert html_obsidian == export_html_bundle(result)

    out_path = tmp_path / "nested" / "codeatlas.html"
    write_export(out_path, html_obsidian)
    raw_bytes = out_path.read_bytes()
    assert b"\r\n" not in raw_bytes
    assert b"\r" not in raw_bytes

    disk_html = raw_bytes.decode("utf-8")
    assert disk_html == html_obsidian

    # Zero external CDNs and zero un-inlined /static/ or /favicon.svg references
    assert "http://" not in disk_html.lower()
    assert "https://" not in disk_html.lower()
    assert "/static/" not in disk_html
    assert "/favicon.svg" not in disk_html

    inspector = _HtmlBundleInspector()
    inspector.feed(disk_html)

    assert inspector.html_attrs.get("data-theme") == "obsidian"
    assert inspector.meta_csp is not None
    assert "frame-ancestors" not in inspector.meta_csp
    assert "default-src 'none'" in inspector.meta_csp
    assert "connect-src 'none'" in inspector.meta_csp
    assert "base-uri 'none'" in inspector.meta_csp
    assert "img-src 'self' data: blob:" in inspector.meta_csp

    # Verify favicon data URI
    icon_links = [link for link in inspector.links if "icon" in (link.get("rel") or "").lower()]
    assert len(icon_links) == 1
    assert (icon_links[0].get("href") or "").startswith("data:image/svg+xml,")
    assert not any((link.get("rel") or "").lower() == "stylesheet" for link in inspector.links)

    # Verify <style> SHA-256 matches style-src
    assert len(inspector.styles) == 1
    style_hash = _sha256_token(inspector.styles[0])
    style_match = re.search(r"style-src\s+([^;]+)", inspector.meta_csp)
    assert style_match is not None
    assert style_hash in style_match.group(1).split()

    # Verify all executable <script> blocks match script-src and have no external src
    script_match = re.search(r"script-src\s+([^;]+)", inspector.meta_csp)
    assert script_match is not None
    allowed_script_hashes = set(script_match.group(1).split())

    exec_scripts = []
    bootstrap_blocks = []
    for attrs, body in inspector.scripts:
        assert "src" not in attrs
        if attrs.get("type") == "application/json":
            bootstrap_blocks.append((attrs, body))
        else:
            exec_scripts.append(body)

    assert len(exec_scripts) == 3
    for script_body in exec_scripts:
        assert _sha256_token(script_body) in allowed_script_hashes

    # Verify bootstrap JSON payload and breakout escaping
    assert len(bootstrap_blocks) == 1
    bs_attrs, bs_body = bootstrap_blocks[0]
    assert bs_attrs.get("id") == "codeatlas-bootstrap"
    assert "</script>" not in bs_body
    assert "\u2028" not in bs_body
    assert "\u2029" not in bs_body

    bootstrap_payload = json.loads(bs_body)
    assert bootstrap_payload["static_mode"] is True
    assert bootstrap_payload["watch_enabled"] is False
    assert bootstrap_payload["repo_name"] == "synthetic-saas"
    assert bootstrap_payload["health"]["grade"] == "C"
    assert any(
        sym["name"] == "danger</script>\u2028\u2029Tag"
        for node in bootstrap_payload["graph"]["nodes"]
        for sym in node["symbols"]
    )

    # Verify Paper theme option sets data-theme="paper"
    html_paper = export_html_bundle(result, default_theme=ThemeMode.PAPER)
    paper_inspector = _HtmlBundleInspector()
    paper_inspector.feed(html_paper)
    assert paper_inspector.html_attrs.get("data-theme") == "paper"


def test_export_analysis_rejects_console_format() -> None:
    result = _make_clean_result()
    with pytest.raises(ValueError, match="Unsupported file export format"):
        export_analysis(result, ExportFormat.CONSOLE)
