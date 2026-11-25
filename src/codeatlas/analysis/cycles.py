"""Iterative Tarjan's Strongly Connected Components cycle detector."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence

from codeatlas.models.analysis import ArchSmell, Cycle, Severity, SmellKind
from codeatlas.models.graph import CodeGraph


def find_strongly_connected_components(
    adjacency: Mapping[str, Sequence[str]],
) -> list[list[str]]:
    """Find all strongly connected components using iterative Tarjan's algorithm.

    This implementation avoids Python recursion entirely so arbitrarily deep
    graphs (e.g., chains of 1,500+ modules) never raise ``RecursionError``.
    Both the vertex list and neighbor lists are sorted for deterministic output.
    """
    vertex_set: set[str] = set(adjacency.keys())
    for targets in adjacency.values():
        vertex_set.update(targets)

    vertices = sorted(vertex_set)
    sorted_adj: dict[str, list[str]] = {
        node: sorted(set(adjacency.get(node, ()))) for node in vertices
    }

    index_counter = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    tarjan_stack: list[str] = []
    sccs: list[list[str]] = []

    for root in vertices:
        if root in indices:
            continue

        indices[root] = lowlink[root] = index_counter
        index_counter += 1
        tarjan_stack.append(root)
        on_stack.add(root)
        call_stack: list[tuple[str, list[str], int]] = [(root, sorted_adj[root], 0)]

        while call_stack:
            u, neighbors, idx = call_stack[-1]
            if idx < len(neighbors):
                v = neighbors[idx]
                call_stack[-1] = (u, neighbors, idx + 1)
                if v not in indices:
                    indices[v] = lowlink[v] = index_counter
                    index_counter += 1
                    tarjan_stack.append(v)
                    on_stack.add(v)
                    call_stack.append((v, sorted_adj[v], 0))
                elif v in on_stack and indices[v] < lowlink[u]:
                    lowlink[u] = indices[v]
            else:
                call_stack.pop()
                if lowlink[u] == indices[u]:
                    component: list[str] = []
                    while True:
                        member = tarjan_stack.pop()
                        on_stack.remove(member)
                        component.append(member)
                        if member == u:
                            break
                    sccs.append(sorted(component))
                if call_stack:
                    parent = call_stack[-1][0]
                    if lowlink[u] < lowlink[parent]:
                        lowlink[parent] = lowlink[u]

    sccs.sort()
    return sccs


def find_shortest_cycle_path(
    component: Sequence[str],
    adjacency: Mapping[str, Sequence[str]],
) -> str:
    """Compute a canonical shortest closed cycle path starting from the smallest node ID.

    Uses BFS within the component so the representative loop ``A -> B -> ... -> A``
    is both minimal in hop count and deterministic across runs.
    """
    if not component:
        return ""

    ordered_nodes = sorted(component)
    start = ordered_nodes[0]
    if len(ordered_nodes) == 1:
        return f"{start} -> {start}"

    comp_set = set(ordered_nodes)
    visited: set[str] = set()
    parent: dict[str, str] = {}
    queue: deque[str] = deque()

    for nxt in sorted(set(adjacency.get(start, ()))):
        if nxt in comp_set and nxt != start and nxt not in visited:
            visited.add(nxt)
            parent[nxt] = start
            queue.append(nxt)

    while queue:
        curr = queue.popleft()
        for nxt in sorted(set(adjacency.get(curr, ()))):
            if nxt not in comp_set:
                continue
            if nxt == start:
                chain = [curr]
                cursor = curr
                while cursor != start:
                    cursor = parent[cursor]
                    chain.append(cursor)
                chain.reverse()
                chain.append(start)
                return " -> ".join(chain)
            if nxt not in visited:
                visited.add(nxt)
                parent[nxt] = curr
                queue.append(nxt)

    return " -> ".join([*ordered_nodes, start])


def detect_cycles(
    graph: CodeGraph,
    *,
    include_self_loops: bool = False,
) -> tuple[list[Cycle], set[tuple[str, str]]]:
    """Detect all dependency cycles in ``graph`` via iterative Tarjan's SCC algorithm.

    Returns a tuple of ``(cycles, cycle_edges)`` where ``cycles`` is sorted
    lexicographically by member node IDs and ``cycle_edges`` contains every
    directed edge participating in a detected cycle.
    """
    adjacency: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        if not include_self_loops and edge.source == edge.target:
            continue
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set())

    sorted_adj: dict[str, list[str]] = {
        node: sorted(targets) for node, targets in adjacency.items()
    }
    raw_sccs = find_strongly_connected_components(sorted_adj)

    qualifying: list[list[str]] = []
    for comp in raw_sccs:
        if len(comp) >= 2 or (
            include_self_loops and len(comp) == 1 and comp[0] in sorted_adj.get(comp[0], [])
        ):
            qualifying.append(comp)

    qualifying.sort()

    cycles: list[Cycle] = []
    all_cycle_edges: set[tuple[str, str]] = set()

    for idx, comp in enumerate(qualifying, start=1):
        comp_set = set(comp)
        comp_edges: list[tuple[str, str]] = []
        for source in comp:
            for target in sorted_adj.get(source, []):
                if target in comp_set and (include_self_loops or source != target):
                    pair = (source, target)
                    comp_edges.append(pair)
                    all_cycle_edges.add(pair)

        comp_edges.sort()
        display_path = find_shortest_cycle_path(comp, sorted_adj)
        cycles.append(
            Cycle(
                id=f"cycle-{idx:03d}",
                nodes=list(comp),
                edges=comp_edges,
                display_path=display_path,
            )
        )

    return cycles, all_cycle_edges


def cycles_to_smells(cycles: Sequence[Cycle]) -> list[ArchSmell]:
    """Convert detected ``Cycle`` objects into ``ArchSmell`` findings."""
    smells: list[ArchSmell] = []
    for cycle in cycles:
        smells.append(
            ArchSmell(
                rule_id="ARCH-CYC-001",
                kind=SmellKind.CIRCULAR_DEPENDENCY,
                severity=Severity.HIGH,
                title=f"Circular dependency ({len(cycle.nodes)} modules)",
                description=f"Strongly connected module cycle detected: {cycle.display_path}.",
                nodes=list(cycle.nodes),
                edges=list(cycle.edges),
                remediation=(
                    "Break the dependency loop by extracting shared abstractions or inverting "
                    "control with an interface/event boundary."
                ),
            )
        )
    return smells
