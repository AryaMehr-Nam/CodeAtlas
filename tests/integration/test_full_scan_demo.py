"""End-to-end integration tests scanning the bundled multi-language demo repository."""

from __future__ import annotations

from pathlib import Path

from codeatlas.analysis import compute_blast_radius, scan_repository, trace_request_to_db
from codeatlas.models.analysis import Severity, SmellKind
from codeatlas.models.config import Language, Layer


def _demo_repo_root() -> Path:
    return Path(__file__).resolve().parents[2] / "demo" / "sample_saas"


def test_full_scan_demo_sample_saas() -> None:
    demo_root = _demo_repo_root()
    assert demo_root.is_dir(), f"Missing demo repository at {demo_root}"

    result = scan_repository(demo_root)
    graph = result.graph
    health = result.health

    assert graph.repo_name == "sample_saas"
    assert graph.truncated is False

    node_by_id = {node.id: node for node in graph.nodes}
    assert len(node_by_id) == 22

    # Multi-language nodes present.
    languages = {node.language for node in graph.nodes}
    assert {Language.PYTHON, Language.TYPESCRIPT, Language.PHP}.issubset(languages)

    # All 5 core application layers populated.
    expected_layers = {
        Layer.ENTRYPOINT,
        Layer.FRONTEND,
        Layer.SERVICE,
        Layer.DATA,
        Layer.SHARED,
    }
    assert expected_layers.issubset(set(health.layer_counts.keys()))
    assert expected_layers.issubset({node.layer for node in graph.nodes})

    # Meaningful cluster grouping across subprojects.
    cluster_ids = {cluster.id for cluster in graph.clusters}
    assert {"backend", "frontend", "worker"}.issubset(cluster_ids)

    # Extracted FastAPI & Laravel routes.
    assert len(node_by_id["backend/auth_routes.py"].routes) >= 3
    assert len(node_by_id["backend/billing_routes.py"].routes) >= 2
    assert len(node_by_id["backend/analytics_routes.py"].routes) >= 1

    worker_routes = node_by_id["worker/src/Routes/worker.php"].routes
    assert len(worker_routes) >= 2
    worker_route_paths = {route.path for route in worker_routes}
    assert "/worker/sync" in worker_route_paths
    assert "/worker/health" in worker_route_paths

    # Cycle detected between billing_service and invoice_service.
    assert any(
        set(cycle.nodes) == {"backend/billing_service.py", "backend/invoice_service.py"}
        for cycle in health.cycles
    )
    cycle_edges = {(edge.source, edge.target) for edge in graph.edges if edge.is_cycle}
    assert ("backend/billing_service.py", "backend/invoice_service.py") in cycle_edges
    assert ("backend/invoice_service.py", "backend/billing_service.py") in cycle_edges
    assert node_by_id["backend/billing_service.py"].health_status == "critical"
    assert node_by_id["backend/invoice_service.py"].health_status == "critical"

    # God Module hotspot detected on analytics_service.py.
    assert "backend/analytics_service.py" in health.hotspots
    assert any(
        smell.rule_id == "ARCH-GOD-001"
        and smell.kind == SmellKind.GOD_MODULE
        and "backend/analytics_service.py" in smell.nodes
        for smell in health.smells
    )

    # Orphan / dead file detected on legacy_exporter.py.
    assert "backend/legacy_exporter.py" in health.dead_files
    assert any(
        smell.rule_id == "ARCH-DEAD-001"
        and smell.kind == SmellKind.DEAD_CODE
        and "backend/legacy_exporter.py" in smell.nodes
        for smell in health.smells
    )

    # Layer violation detected from PHP Model to Service.
    expected_viol_edge = (
        "worker/src/Models/PaymentRecord.php",
        "worker/src/Services/NotificationService.php",
    )
    assert any(
        smell.rule_id == "ARCH-LAY-001"
        and smell.kind == SmellKind.LAYER_VIOLATION
        and smell.severity == Severity.HIGH
        and expected_viol_edge in smell.edges
        for smell in health.smells
    )
    assert any(
        (edge.source, edge.target) == expected_viol_edge and edge.is_violation
        for edge in graph.edges
    )

    # Blast radius and request-to-DB tracing work cleanly on scanned graph.
    blast = compute_blast_radius(graph, "backend/user_model.py", max_depth=4)
    upstream_ids = {item.node_id for item in blast.upstream}
    assert "backend/auth_service.py" in upstream_ids
    assert "backend/auth_routes.py" in upstream_ids

    trace = trace_request_to_db(graph, "backend/auth_routes.py", max_depth=6)
    assert "backend/user_model.py" in trace.data_targets
    assert "backend/db.py" in trace.data_targets

    # Health scorecard sanity.
    assert 0.0 < health.score < 100.0
    assert health.breakdown.cycle_penalty > 0.0
    assert health.breakdown.violation_penalty > 0.0
    assert health.breakdown.god_penalty > 0.0
    assert health.breakdown.dead_penalty > 0.0

    # Deterministic output: repeated scans must produce byte-identical JSON.
    second_result = scan_repository(demo_root)
    assert result.model_dump_json() == second_result.model_dump_json()
