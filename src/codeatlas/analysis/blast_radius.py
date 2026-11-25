"""Blast radius BFS impact analysis and request-to-DB execution flow tracing."""

from __future__ import annotations

from collections import deque

from codeatlas.models.analysis import BlastRadius, FlowTrace, ImpactNode
from codeatlas.models.config import Layer
from codeatlas.models.graph import CodeGraph, Node, normalize_posix_rel_path


def _build_adjacency(
    graph: CodeGraph,
) -> tuple[dict[str, Node], dict[str, list[str]], dict[str, list[str]]]:
    """Build node lookup plus sorted forward and reverse adjacency maps."""
    node_by_id = {node.id: node for node in graph.nodes}
    forward_sets: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    reverse_sets: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}

    for edge in graph.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in node_by_id or edge.target not in node_by_id:
            continue
        forward_sets[edge.source].add(edge.target)
        reverse_sets[edge.target].add(edge.source)

    forward_adj = {node_id: sorted(targets) for node_id, targets in forward_sets.items()}
    reverse_adj = {node_id: sorted(sources) for node_id, sources in reverse_sets.items()}
    return node_by_id, forward_adj, reverse_adj


def _bfs_impact(
    start_id: str,
    adjacency: dict[str, list[str]],
    max_depth: int,
    *,
    reverse_path: bool,
) -> list[ImpactNode]:
    """Run cycle-safe BFS from ``start_id`` up to ``max_depth`` hops."""
    if max_depth <= 0:
        return []

    visited: set[str] = {start_id}
    queue: deque[tuple[str, int, list[str]]] = deque([(start_id, 0, [start_id])])
    impacts: list[ImpactNode] = []

    while queue:
        curr, depth, path = queue.popleft()
        if depth >= max_depth:
            continue
        for nxt in adjacency.get(curr, ()):
            if nxt in visited:
                continue
            visited.add(nxt)
            next_depth = depth + 1
            next_path = [nxt, *path] if reverse_path else [*path, nxt]
            impacts.append(ImpactNode(node_id=nxt, depth=next_depth, path=next_path))
            queue.append((nxt, next_depth, next_path))

    impacts.sort(key=lambda item: (item.depth, item.node_id))
    return impacts


def compute_blast_radius(
    graph: CodeGraph,
    target_id: str,
    max_depth: int = 5,
) -> BlastRadius:
    """Compute upstream callers and downstream dependencies of ``target_id``.

    Uses cycle-safe breadth-first search up to ``max_depth`` hops with
    deterministic ordering by ``(depth, node_id)``.
    """
    if max_depth < 0:
        raise ValueError(f"max_depth must be >= 0, got {max_depth}")

    norm_target = normalize_posix_rel_path(target_id)
    node_by_id, forward_adj, reverse_adj = _build_adjacency(graph)
    if norm_target not in node_by_id:
        return BlastRadius(
            target_id=norm_target,
            upstream=[],
            downstream=[],
            upstream_count=0,
            downstream_count=0,
        )

    upstream = _bfs_impact(norm_target, reverse_adj, max_depth, reverse_path=True)
    downstream = _bfs_impact(norm_target, forward_adj, max_depth, reverse_path=False)
    return BlastRadius(
        target_id=norm_target,
        upstream=upstream,
        downstream=downstream,
        upstream_count=len(upstream),
        downstream_count=len(downstream),
    )


def _is_data_target(node: Node) -> bool:
    """Return True when a node belongs to the data layer or accesses a database."""
    return node.layer == Layer.DATA or node.has_db_access


def trace_request_to_db(
    graph: CodeGraph,
    entrypoint_id: str,
    max_depth: int = 8,
) -> FlowTrace:
    """Trace shortest dependency paths from an entrypoint to reachable DB/data nodes."""
    if max_depth < 0:
        raise ValueError(f"max_depth must be >= 0, got {max_depth}")

    norm_entry = normalize_posix_rel_path(entrypoint_id)
    node_by_id, forward_adj, _ = _build_adjacency(graph)
    if norm_entry not in node_by_id:
        return FlowTrace(entrypoint_id=norm_entry)

    visited: set[str] = {norm_entry}
    paths_found: list[list[str]] = []
    if _is_data_target(node_by_id[norm_entry]):
        paths_found.append([norm_entry])

    if max_depth > 0:
        queue: deque[tuple[str, int, list[str]]] = deque([(norm_entry, 0, [norm_entry])])
        while queue:
            curr, depth, path = queue.popleft()
            if depth >= max_depth:
                continue
            for nxt in forward_adj.get(curr, ()):
                if nxt in visited:
                    continue
                visited.add(nxt)
                next_path = [*path, nxt]
                target_node = node_by_id.get(nxt)
                if target_node is not None and _is_data_target(target_node):
                    paths_found.append(next_path)
                queue.append((nxt, depth + 1, next_path))

    paths_found.sort(key=lambda path: (len(path), tuple(path)))
    data_targets = [path[-1] for path in paths_found]
    highlighted_nodes = sorted({node_id for path in paths_found for node_id in path})
    highlighted_edges = sorted(
        {(path[idx], path[idx + 1]) for path in paths_found for idx in range(len(path) - 1)}
    )

    return FlowTrace(
        entrypoint_id=norm_entry,
        data_targets=data_targets,
        paths=paths_found,
        highlighted_nodes=highlighted_nodes,
        highlighted_edges=highlighted_edges,
    )
