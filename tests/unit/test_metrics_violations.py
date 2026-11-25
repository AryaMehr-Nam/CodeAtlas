"""Unit tests for layer classification, metrics, hotspots, dead files, and violations."""

from __future__ import annotations

import pytest

from codeatlas.analysis.layers import classify_layer
from codeatlas.analysis.metrics import (
    analyze_metrics,
    compute_node_metrics,
    detect_dead_files,
    detect_god_modules,
)
from codeatlas.analysis.violations import LAYER_VIOLATION_RULES, detect_layer_violations
from codeatlas.models.analysis import Severity, SmellKind
from codeatlas.models.config import AnalysisThresholds, Language, Layer
from codeatlas.models.graph import CodeGraph, Edge, Node, RouteInfo


def _node(
    path: str,
    *,
    layer: Layer = Layer.SHARED,
    language: Language = Language.PYTHON,
    loc: int = 20,
    complexity: int = 2,
    is_entrypoint: bool = False,
    has_db_access: bool = False,
    routes: list[RouteInfo] | None = None,
) -> Node:
    return Node(
        id=path,
        path=path,
        language=language,
        layer=layer,
        loc=loc,
        complexity=complexity,
        is_entrypoint=is_entrypoint,
        has_db_access=has_db_access,
        routes=routes or [],
    )


def test_classify_layer_covers_all_seven_layers_and_precedence() -> None:
    # Test overrides all AST cues.
    assert (
        classify_layer(
            "tests/test_billing.py",
            Language.PYTHON,
            is_entrypoint=True,
            has_routes=True,
            has_db_access=True,
        )
        == Layer.TEST
    )
    assert classify_layer("frontend/Button.test.tsx", Language.TYPESCRIPT) == Layer.TEST
    assert classify_layer("worker/tests/BillingServiceTest.php", Language.PHP) == Layer.TEST
    assert classify_layer("pkg/auth/handler_test.go", Language.GO) == Layer.TEST
    # Ensure non-test substrings like "contest.py" do not match test.
    assert classify_layer("src/contest.py", Language.PYTHON) == Layer.SHARED

    # Config overrides DB/shared cues.
    assert classify_layer("src/config/database.py", Language.PYTHON, has_db_access=True) == (
        Layer.CONFIG
    )
    assert classify_layer("frontend/vite.config.ts", Language.TYPESCRIPT) == Layer.CONFIG
    assert classify_layer("infra/settings.py", Language.PYTHON) == Layer.CONFIG

    # Entrypoint from AST cues or route paths.
    assert classify_layer("backend/auth_routes.py", Language.PYTHON, has_routes=True) == (
        Layer.ENTRYPOINT
    )
    assert classify_layer("worker/src/Routes/worker.php", Language.PHP, has_routes=True) == (
        Layer.ENTRYPOINT
    )
    assert classify_layer("cmd/api/main.go", Language.GO, is_entrypoint=True) == Layer.ENTRYPOINT

    # Frontend workspace files (including client.ts and pages/components).
    assert classify_layer("frontend/src/pages/DashboardPage.tsx", Language.TYPESCRIPT) == (
        Layer.FRONTEND
    )
    assert classify_layer("frontend/src/api/client.ts", Language.TYPESCRIPT) == Layer.FRONTEND
    assert classify_layer("frontend/src/types/billing.ts", Language.TYPESCRIPT) == Layer.FRONTEND
    assert classify_layer("src/pages/api/health.ts", Language.TYPESCRIPT) == Layer.ENTRYPOINT

    # Data vs Service innermost path specificity (including worker/Models and db_access services).
    assert classify_layer("worker/src/Models/PaymentRecord.php", Language.PHP) == Layer.DATA
    assert classify_layer("backend/user_model.py", Language.PYTHON, has_db_access=True) == (
        Layer.DATA
    )
    assert classify_layer("backend/persistence.py", Language.PYTHON, has_db_access=True) == (
        Layer.DATA
    )
    assert classify_layer("backend/auth_service.py", Language.PYTHON, has_db_access=True) == (
        Layer.SERVICE
    )
    assert classify_layer("worker/src/Jobs/BillingSyncJob.php", Language.PHP) == Layer.SERVICE
    assert classify_layer("worker/src/Services/NotificationService.php", Language.PHP) == (
        Layer.SERVICE
    )

    # Shared fallback.
    assert classify_layer("backend/crypto.py", Language.PYTHON) == Layer.SHARED
    assert classify_layer("src/utils/formatting.ts", Language.TYPESCRIPT) == Layer.SHARED


def test_fan_in_fan_out_and_instability_calculation() -> None:
    graph = CodeGraph(
        repo_name="metrics-repo",
        root_path="/repo",
        nodes=[
            _node("entry.py", layer=Layer.ENTRYPOINT),
            _node("svc.py", layer=Layer.SERVICE),
            _node("db.py", layer=Layer.DATA),
            _node("util.py", layer=Layer.SHARED),
            _node("isolated.py", layer=Layer.SHARED),
        ],
        edges=[
            Edge(source="entry.py", target="svc.py"),
            Edge(source="svc.py", target="db.py"),
            Edge(source="svc.py", target="util.py"),
            # Duplicate edge & self-loop must not inflate counts.
            Edge(source="svc.py", target="db.py"),
            Edge(source="svc.py", target="svc.py"),
        ],
    )

    compute_node_metrics(graph)
    by_id = {n.id: n for n in graph.nodes}

    assert by_id["entry.py"].fan_in == 0
    assert by_id["entry.py"].fan_out == 1
    assert by_id["entry.py"].instability == 1.0

    assert by_id["svc.py"].fan_in == 1
    assert by_id["svc.py"].fan_out == 2
    assert by_id["svc.py"].instability == 0.6667

    assert by_id["db.py"].fan_in == 1
    assert by_id["db.py"].fan_out == 0
    assert by_id["db.py"].instability == 0.0

    assert by_id["isolated.py"].fan_in == 0
    assert by_id["isolated.py"].fan_out == 0
    assert by_id["isolated.py"].instability == 0.0


def test_god_module_thresholds_and_exemptions() -> None:
    thresholds = AnalysisThresholds(god_loc=350, god_fan_out=10, god_fan_in=12, god_complexity=25)
    nodes = [
        _node("loc_god.py", layer=Layer.SERVICE, loc=350, complexity=5),
        _node("cx_god.py", layer=Layer.SERVICE, loc=100, complexity=25),
        _node("fanout_god.py", layer=Layer.SERVICE, loc=50, complexity=3),
        _node("fanin_god.py", layer=Layer.DATA, loc=50, complexity=3),
        _node("just_below.py", layer=Layer.SERVICE, loc=349, complexity=24),
        # Test and config nodes are exempt even when huge.
        _node("tests/test_big.py", layer=Layer.TEST, loc=900, complexity=80),
        _node("config/settings.py", layer=Layer.CONFIG, loc=600, complexity=40),
    ]
    # Add 10 distinct callees for fanout_god and 12 distinct callers for fanin_god.
    edges: list[Edge] = []
    for idx in range(10):
        callee = f"dep_{idx}.py"
        nodes.append(_node(callee, layer=Layer.SHARED))
        edges.append(Edge(source="fanout_god.py", target=callee))
    for idx in range(12):
        caller = f"caller_{idx}.py"
        nodes.append(_node(caller, layer=Layer.SERVICE))
        edges.append(Edge(source=caller, target="fanin_god.py"))

    graph = CodeGraph(repo_name="god-repo", root_path="/repo", nodes=nodes, edges=edges)
    compute_node_metrics(graph)
    smells, hotspots = detect_god_modules(graph, thresholds=thresholds)

    assert hotspots == ["cx_god.py", "fanin_god.py", "fanout_god.py", "loc_god.py"]
    assert len(smells) == 4
    for smell in smells:
        assert smell.rule_id == "ARCH-GOD-001"
        assert smell.kind == SmellKind.GOD_MODULE
        assert smell.severity == Severity.MEDIUM


def test_dead_file_detection_and_exemptions() -> None:
    nodes = [
        _node("backend/legacy_exporter.py", layer=Layer.SHARED),
        _node("backend/unused_service.py", layer=Layer.SERVICE),
        _node("backend/used_helper.py", layer=Layer.SHARED),
        # Exempt files with fan_in == 0:
        _node("backend/routes.py", layer=Layer.ENTRYPOINT, is_entrypoint=True),
        _node(
            "backend/routed.py",
            layer=Layer.SERVICE,
            routes=[RouteInfo(method="GET", path="/ping", handler="ping", line=1)],
        ),
        _node("tests/test_app.py", layer=Layer.TEST),
        _node("config/settings.py", layer=Layer.CONFIG),
        _node("backend/__init__.py", layer=Layer.SHARED),
        _node("backend/__main__.py", layer=Layer.SHARED),
        _node("frontend/src/index.ts", layer=Layer.SHARED, language=Language.TYPESCRIPT),
        _node(
            "frontend/src/components/index.tsx",
            layer=Layer.FRONTEND,
            language=Language.TYPESCRIPT,
        ),
        _node(
            "frontend/src/pages/Dashboard.tsx",
            layer=Layer.FRONTEND,
            language=Language.TYPESCRIPT,
        ),
        _node("frontend/src/App.tsx", layer=Layer.FRONTEND, language=Language.TYPESCRIPT),
        _node("frontend/src/main.tsx", layer=Layer.FRONTEND, language=Language.TYPESCRIPT),
    ]
    graph = CodeGraph(
        repo_name="dead-repo",
        root_path="/repo",
        nodes=nodes,
        edges=[Edge(source="backend/routes.py", target="backend/used_helper.py")],
    )

    summary = analyze_metrics(graph)
    assert summary.dead_files == ["backend/legacy_exporter.py", "backend/unused_service.py"]

    dead_smells, dead_files = detect_dead_files(graph)
    assert dead_files == ["backend/legacy_exporter.py", "backend/unused_service.py"]
    assert len(dead_smells) == 2
    for smell in dead_smells:
        assert smell.rule_id == "ARCH-DEAD-001"
        assert smell.kind == SmellKind.DEAD_CODE
        assert smell.severity == Severity.LOW


@pytest.mark.parametrize(
    ("source_layer", "target_layer", "expected_severity"),
    [
        (Layer.DATA, Layer.ENTRYPOINT, Severity.HIGH),
        (Layer.DATA, Layer.SERVICE, Severity.HIGH),
        (Layer.SHARED, Layer.SERVICE, Severity.HIGH),
        (Layer.SHARED, Layer.ENTRYPOINT, Severity.HIGH),
        (Layer.FRONTEND, Layer.DATA, Severity.HIGH),
        (Layer.SHARED, Layer.DATA, Severity.MEDIUM),
        (Layer.SERVICE, Layer.ENTRYPOINT, Severity.MEDIUM),
    ],
)
def test_layer_violation_severity_pairs(
    source_layer: Layer,
    target_layer: Layer,
    expected_severity: Severity,
) -> None:
    graph = CodeGraph(
        repo_name="viol-repo",
        root_path="/repo",
        nodes=[
            _node("src/source_mod.py", layer=source_layer),
            _node("src/target_mod.py", layer=target_layer),
        ],
        edges=[Edge(source="src/source_mod.py", target="src/target_mod.py")],
    )

    smells, violating_edges = detect_layer_violations(graph)
    assert len(smells) == 1
    assert smells[0].rule_id == "ARCH-LAY-001"
    assert smells[0].kind == SmellKind.LAYER_VIOLATION
    assert smells[0].severity == expected_severity
    assert smells[0].nodes == ["src/source_mod.py", "src/target_mod.py"]
    assert violating_edges == {("src/source_mod.py", "src/target_mod.py")}


def test_allowed_layer_dependencies_produce_no_violations_and_all_rules_sorted() -> None:
    # Allowed dependencies should not trigger ARCH-LAY-001.
    clean_graph = CodeGraph(
        repo_name="clean-repo",
        root_path="/repo",
        nodes=[
            _node("fe.tsx", layer=Layer.FRONTEND, language=Language.TYPESCRIPT),
            _node("ep.py", layer=Layer.ENTRYPOINT),
            _node("svc.py", layer=Layer.SERVICE),
            _node("data.py", layer=Layer.DATA),
            _node("shared.py", layer=Layer.SHARED),
            _node("test.py", layer=Layer.TEST),
        ],
        edges=[
            Edge(source="fe.tsx", target="ep.py"),
            Edge(source="fe.tsx", target="shared.py"),
            Edge(source="ep.py", target="svc.py"),
            Edge(source="ep.py", target="data.py"),
            Edge(source="ep.py", target="shared.py"),
            Edge(source="svc.py", target="data.py"),
            Edge(source="svc.py", target="shared.py"),
            Edge(source="data.py", target="shared.py"),
            Edge(source="test.py", target="data.py"),
            Edge(source="test.py", target="ep.py"),
        ],
    )
    clean_smells, clean_edges = detect_layer_violations(clean_graph)
    assert clean_smells == []
    assert clean_edges == set()

    # Verify ordering: HIGH violations precede MEDIUM violations.
    mixed_graph = CodeGraph(
        repo_name="mixed-repo",
        root_path="/repo",
        nodes=[
            _node("a_svc.py", layer=Layer.SERVICE),
            _node("b_ep.py", layer=Layer.ENTRYPOINT),
            _node("z_data.py", layer=Layer.DATA),
        ],
        edges=[
            Edge(source="a_svc.py", target="b_ep.py"),
            Edge(source="z_data.py", target="a_svc.py"),
        ],
    )
    mixed_smells, mixed_edges = detect_layer_violations(mixed_graph)
    assert len(mixed_smells) == 2
    assert len(LAYER_VIOLATION_RULES) == 7
    assert mixed_smells[0].severity == Severity.HIGH
    assert mixed_smells[0].edges == [("z_data.py", "a_svc.py")]
    assert mixed_smells[1].severity == Severity.MEDIUM
    assert mixed_smells[1].edges == [("a_svc.py", "b_ep.py")]
    assert mixed_edges == {("a_svc.py", "b_ep.py"), ("z_data.py", "a_svc.py")}
