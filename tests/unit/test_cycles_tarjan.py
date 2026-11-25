"""Unit tests for iterative Tarjan's SCC cycle detection."""

from __future__ import annotations

import pytest

from codeatlas.analysis.cycles import (
    cycles_to_smells,
    detect_cycles,
    find_strongly_connected_components,
)
from codeatlas.models.analysis import Severity, SmellKind
from codeatlas.models.config import Language, Layer
from codeatlas.models.graph import CodeGraph, Edge, Node


def _make_node(node_id: str, layer: Layer = Layer.SERVICE) -> Node:
    return Node(
        id=node_id,
        path=node_id,
        language=Language.PYTHON,
        layer=layer,
    )


def _make_graph(node_ids: list[str], pairs: list[tuple[str, str]]) -> CodeGraph:
    return CodeGraph(
        repo_name="test-repo",
        root_path="/repo",
        nodes=[_make_node(node_id) for node_id in node_ids],
        edges=[Edge(source=src, target=dst) for src, dst in pairs],
    )


def test_acyclic_and_empty_graphs_have_no_cycles() -> None:
    empty_graph = _make_graph([], [])
    cycles, cycle_edges = detect_cycles(empty_graph)
    assert cycles == []
    assert cycle_edges == set()

    dag = _make_graph(
        ["a.py", "b.py", "c.py", "d.py"],
        [("a.py", "b.py"), ("a.py", "c.py"), ("b.py", "d.py"), ("c.py", "d.py")],
    )
    cycles, cycle_edges = detect_cycles(dag)
    assert cycles == []
    assert cycle_edges == set()


def test_simple_two_node_cycle() -> None:
    graph = _make_graph(
        ["billing_service.py", "invoice_service.py"],
        [
            ("invoice_service.py", "billing_service.py"),
            ("billing_service.py", "invoice_service.py"),
        ],
    )
    cycles, cycle_edges = detect_cycles(graph)

    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.id == "cycle-001"
    assert cycle.nodes == ["billing_service.py", "invoice_service.py"]
    assert cycle.edges == [
        ("billing_service.py", "invoice_service.py"),
        ("invoice_service.py", "billing_service.py"),
    ]
    assert cycle.display_path == "billing_service.py -> invoice_service.py -> billing_service.py"
    assert cycle_edges == {
        ("billing_service.py", "invoice_service.py"),
        ("invoice_service.py", "billing_service.py"),
    }


def test_three_node_cycle_ignores_non_cycle_tail_and_head() -> None:
    graph = _make_graph(
        ["entry.py", "c.py", "a.py", "b.py", "leaf.py"],
        [
            ("entry.py", "c.py"),
            ("c.py", "a.py"),
            ("a.py", "b.py"),
            ("b.py", "c.py"),
            ("b.py", "leaf.py"),
        ],
    )
    cycles, cycle_edges = detect_cycles(graph)

    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.nodes == ["a.py", "b.py", "c.py"]
    assert cycle.display_path == "a.py -> b.py -> c.py -> a.py"
    assert cycle_edges == {
        ("a.py", "b.py"),
        ("b.py", "c.py"),
        ("c.py", "a.py"),
    }


def test_overlapping_figure_eight_scc_and_shortest_display_loop() -> None:
    # Figure-8 sharing node "a": 2-loop (a <-> b) and 3-loop (a -> c -> d -> a).
    graph = _make_graph(
        ["d", "c", "b", "a"],
        [
            ("a", "b"),
            ("b", "a"),
            ("a", "c"),
            ("c", "d"),
            ("d", "a"),
        ],
    )
    cycles, cycle_edges = detect_cycles(graph)

    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.nodes == ["a", "b", "c", "d"]
    assert cycle.display_path == "a -> b -> a"
    assert cycle_edges == {
        ("a", "b"),
        ("b", "a"),
        ("a", "c"),
        ("c", "d"),
        ("d", "a"),
    }


def test_disconnected_sccs_and_deterministic_ordering() -> None:
    # Build two graphs with reversed insertion orders to verify determinism.
    nodes_fwd = ["z", "y", "x", "m", "b", "a"]
    edges_fwd = [
        ("x", "y"),
        ("y", "z"),
        ("z", "x"),
        ("b", "a"),
        ("a", "b"),
        ("m", "x"),
    ]
    nodes_rev = list(reversed(nodes_fwd))
    edges_rev = list(reversed(edges_fwd))

    cycles_a, edges_a = detect_cycles(_make_graph(nodes_fwd, edges_fwd))
    cycles_b, edges_b = detect_cycles(_make_graph(nodes_rev, edges_rev))

    assert cycles_a == cycles_b
    assert edges_a == edges_b
    assert len(cycles_a) == 2

    assert cycles_a[0].id == "cycle-001"
    assert cycles_a[0].nodes == ["a", "b"]
    assert cycles_a[0].display_path == "a -> b -> a"

    assert cycles_a[1].id == "cycle-002"
    assert cycles_a[1].nodes == ["x", "y", "z"]
    assert cycles_a[1].display_path == "x -> y -> z -> x"


def test_self_loop_behavior() -> None:
    graph = _make_graph(["solo.py", "other.py"], [("solo.py", "solo.py"), ("solo.py", "other.py")])

    default_cycles, default_edges = detect_cycles(graph)
    assert default_cycles == []
    assert default_edges == set()

    with_self, with_self_edges = detect_cycles(graph, include_self_loops=True)
    assert len(with_self) == 1
    assert with_self[0].nodes == ["solo.py"]
    assert with_self[0].edges == [("solo.py", "solo.py")]
    assert with_self[0].display_path == "solo.py -> solo.py"
    assert with_self_edges == {("solo.py", "solo.py")}


@pytest.mark.parametrize("chain_len", [1600, 2200])
def test_large_chain_and_large_cycle_no_recursion_error(chain_len: int) -> None:
    # Linear chain of > 1500 nodes has no cycles and must not hit RecursionError.
    node_ids = [f"mod_{idx:04d}.py" for idx in range(chain_len)]
    chain_edges = [(node_ids[idx], node_ids[idx + 1]) for idx in range(chain_len - 1)]
    chain_graph = _make_graph(node_ids, chain_edges)

    sccs = find_strongly_connected_components(
        {node_ids[i]: [node_ids[i + 1]] for i in range(chain_len - 1)}
    )
    assert len(sccs) == chain_len

    cycles, cycle_edges = detect_cycles(chain_graph)
    assert cycles == []
    assert cycle_edges == set()

    # Closing the chain into one giant cycle must also run iteratively without RecursionError.
    loop_edges = [*chain_edges, (node_ids[-1], node_ids[0])]
    loop_graph = _make_graph(node_ids, loop_edges)
    loop_cycles, loop_cycle_edges = detect_cycles(loop_graph)

    assert len(loop_cycles) == 1
    assert len(loop_cycles[0].nodes) == chain_len
    assert len(loop_cycle_edges) == chain_len
    assert loop_cycles[0].display_path.startswith("mod_0000.py -> mod_0001.py")
    assert loop_cycles[0].display_path.endswith("-> mod_0000.py")


def test_cycles_to_smells_conversion() -> None:
    graph = _make_graph(["a.py", "b.py"], [("a.py", "b.py"), ("b.py", "a.py")])
    cycles, _ = detect_cycles(graph)
    smells = cycles_to_smells(cycles)

    assert len(smells) == 1
    smell = smells[0]
    assert smell.rule_id == "ARCH-CYC-001"
    assert smell.kind == SmellKind.CIRCULAR_DEPENDENCY
    assert smell.severity == Severity.HIGH
    assert smell.nodes == ["a.py", "b.py"]
    assert smell.edges == [("a.py", "b.py"), ("b.py", "a.py")]
