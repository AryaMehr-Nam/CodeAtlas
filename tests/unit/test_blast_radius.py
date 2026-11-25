"""Unit tests for BFS blast radius impact analysis and request-to-DB tracing."""

from __future__ import annotations

import pytest

from codeatlas.analysis.blast_radius import compute_blast_radius, trace_request_to_db
from codeatlas.models.config import Language, Layer
from codeatlas.models.graph import CodeGraph, Edge, Node


def _node(
    node_id: str,
    layer: Layer = Layer.SERVICE,
    *,
    has_db_access: bool = False,
) -> Node:
    return Node(
        id=node_id,
        path=node_id,
        language=Language.PYTHON,
        layer=layer,
        has_db_access=has_db_access,
    )


def test_multi_hop_blast_radius_upstream_and_downstream() -> None:
    graph = CodeGraph(
        repo_name="blast-repo",
        root_path="/repo",
        nodes=[
            _node("routes_a.py", Layer.ENTRYPOINT),
            _node("routes_b.py", Layer.ENTRYPOINT),
            _node("auth_svc.py", Layer.SERVICE),
            _node("target_svc.py", Layer.SERVICE),
            _node("repo.py", Layer.DATA),
            _node("logger.py", Layer.SHARED),
            _node("db.py", Layer.DATA),
        ],
        edges=[
            Edge(source="routes_a.py", target="auth_svc.py"),
            Edge(source="routes_b.py", target="target_svc.py"),
            Edge(source="auth_svc.py", target="target_svc.py"),
            Edge(source="target_svc.py", target="repo.py"),
            Edge(source="target_svc.py", target="logger.py"),
            Edge(source="repo.py", target="db.py"),
        ],
    )

    blast = compute_blast_radius(graph, "target_svc.py", max_depth=5)
    assert blast.target_id == "target_svc.py"
    assert blast.upstream_count == 3
    assert blast.downstream_count == 3

    assert [(item.node_id, item.depth, item.path) for item in blast.upstream] == [
        ("auth_svc.py", 1, ["auth_svc.py", "target_svc.py"]),
        ("routes_b.py", 1, ["routes_b.py", "target_svc.py"]),
        ("routes_a.py", 2, ["routes_a.py", "auth_svc.py", "target_svc.py"]),
    ]

    assert [(item.node_id, item.depth, item.path) for item in blast.downstream] == [
        ("logger.py", 1, ["target_svc.py", "logger.py"]),
        ("repo.py", 1, ["target_svc.py", "repo.py"]),
        ("db.py", 2, ["target_svc.py", "repo.py", "db.py"]),
    ]


def test_blast_radius_depth_limits_and_edge_cases() -> None:
    graph = CodeGraph(
        repo_name="depth-repo",
        root_path="/repo",
        nodes=[_node("a.py"), _node("b.py"), _node("c.py")],
        edges=[Edge(source="a.py", target="b.py"), Edge(source="b.py", target="c.py")],
    )

    depth_1 = compute_blast_radius(graph, "a.py", max_depth=1)
    assert [item.node_id for item in depth_1.downstream] == ["b.py"]
    assert depth_1.downstream_count == 1

    depth_0 = compute_blast_radius(graph, "a.py", max_depth=0)
    assert depth_0.upstream == []
    assert depth_0.downstream == []

    missing = compute_blast_radius(graph, "nonexistent.py")
    assert missing.target_id == "nonexistent.py"
    assert missing.upstream_count == 0
    assert missing.downstream_count == 0

    with pytest.raises(ValueError, match="max_depth"):
        compute_blast_radius(graph, "a.py", max_depth=-1)


def test_blast_radius_handles_cycles_without_revisiting_target() -> None:
    # Cycle: a -> b -> c -> a, plus x -> b and c -> y
    graph = CodeGraph(
        repo_name="cycle-blast",
        root_path="/repo",
        nodes=[_node("a"), _node("b"), _node("c"), _node("x"), _node("y")],
        edges=[
            Edge(source="a", target="b"),
            Edge(source="b", target="c"),
            Edge(source="c", target="a"),
            Edge(source="x", target="b"),
            Edge(source="c", target="y"),
            Edge(source="a", target="a"),
        ],
    )

    blast = compute_blast_radius(graph, "a", max_depth=5)
    assert [(item.node_id, item.depth) for item in blast.downstream] == [
        ("b", 1),
        ("c", 2),
        ("y", 3),
    ]
    assert [(item.node_id, item.depth) for item in blast.upstream] == [
        ("c", 1),
        ("b", 2),
        ("x", 3),
    ]
    assert all(item.node_id != "a" for item in blast.downstream)
    assert all(item.node_id != "a" for item in blast.upstream)


def test_trace_request_to_db_shortest_paths_and_highlighting() -> None:
    graph = CodeGraph(
        repo_name="trace-repo",
        root_path="/repo",
        nodes=[
            _node("routes.py", Layer.ENTRYPOINT),
            _node("fast_svc.py", Layer.SERVICE),
            _node("slow_svc.py", Layer.SERVICE, has_db_access=True),
            _node("logger.py", Layer.SHARED),
            _node("user_model.py", Layer.DATA),
            _node("db.py", Layer.DATA),
        ],
        edges=[
            Edge(source="routes.py", target="fast_svc.py"),
            Edge(source="routes.py", target="slow_svc.py"),
            Edge(source="routes.py", target="logger.py"),
            Edge(source="fast_svc.py", target="db.py"),
            Edge(source="fast_svc.py", target="logger.py"),
            Edge(source="slow_svc.py", target="user_model.py"),
            Edge(source="user_model.py", target="db.py"),
            # Cycle back to entrypoint should not loop.
            Edge(source="user_model.py", target="routes.py"),
        ],
    )

    trace = trace_request_to_db(graph, "routes.py", max_depth=8)
    assert trace.entrypoint_id == "routes.py"
    assert trace.paths == [
        ["routes.py", "slow_svc.py"],
        ["routes.py", "fast_svc.py", "db.py"],
        ["routes.py", "slow_svc.py", "user_model.py"],
    ]
    assert trace.data_targets == ["slow_svc.py", "db.py", "user_model.py"]
    assert trace.highlighted_nodes == [
        "db.py",
        "fast_svc.py",
        "routes.py",
        "slow_svc.py",
        "user_model.py",
    ]
    assert "logger.py" not in trace.highlighted_nodes
    assert trace.highlighted_edges == [
        ("fast_svc.py", "db.py"),
        ("routes.py", "fast_svc.py"),
        ("routes.py", "slow_svc.py"),
        ("slow_svc.py", "user_model.py"),
    ]


def test_trace_request_to_db_direct_db_entrypoint_and_missing_node() -> None:
    graph = CodeGraph(
        repo_name="direct-db",
        root_path="/repo",
        nodes=[
            _node("direct_route.py", Layer.ENTRYPOINT, has_db_access=True),
            _node("db.py", Layer.DATA),
            _node("orphan_route.py", Layer.ENTRYPOINT),
        ],
        edges=[Edge(source="direct_route.py", target="db.py")],
    )

    direct = trace_request_to_db(graph, "direct_route.py")
    assert direct.paths == [["direct_route.py"], ["direct_route.py", "db.py"]]
    assert direct.data_targets == ["direct_route.py", "db.py"]
    assert direct.highlighted_edges == [("direct_route.py", "db.py")]

    no_db = trace_request_to_db(graph, "orphan_route.py")
    assert no_db.paths == []
    assert no_db.data_targets == []
    assert no_db.highlighted_nodes == []
    assert no_db.highlighted_edges == []

    missing = trace_request_to_db(graph, "missing.py")
    assert missing.entrypoint_id == "missing.py"
    assert missing.paths == []

    with pytest.raises(ValueError, match="max_depth"):
        trace_request_to_db(graph, "direct_route.py", max_depth=-2)
