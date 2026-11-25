"""Token-budgeted dense AI codebase context map (llms.txt) exporter."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from codeatlas.exporters._common import (
    LAYER_ORDER,
    collect_cycle_edges,
    collect_violation_edges,
)
from codeatlas.models.analysis import AnalysisResult, ArchSmell, Severity, SmellKind
from codeatlas.models.config import Layer
from codeatlas.models.graph import Node, RouteInfo, SymbolKind

_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.HIGH: 0,
    Severity.MEDIUM: 1,
    Severity.LOW: 2,
}


def estimate_tokens(text: str) -> float:
    """Estimate LLM token count conservatively using ``len(text) / 4``."""
    return len(text) / 4.0


class _BudgetWriter:
    """Line-by-line text accumulator enforcing a strict ``max_tokens * 4`` char cap."""

    def __init__(self, max_tokens: int) -> None:
        if max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {max_tokens}")
        self.max_chars: int = max_tokens * 4
        self._lines: list[str] = []
        self._char_len: int = 0

    def can_fit(self, line: str) -> bool:
        return self._char_len + len(line) + 1 <= self.max_chars

    def add(self, line: str) -> bool:
        clean = line.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        if not self.can_fit(clean):
            return False
        self._lines.append(clean)
        self._char_len += len(clean) + 1
        return True

    def add_section(self, header: str, body_lines: Sequence[str]) -> tuple[int, int]:
        """Add ``header`` and as many ``body_lines`` as fit within the remaining budget.

        Returns ``(added_count, omitted_count)``.
        """
        if not body_lines:
            return (0, 0)
        first = body_lines[0].replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        if self._char_len + len(header) + 1 + len(first) + 1 > self.max_chars:
            return (0, len(body_lines))
        self.add(header)
        added = 0
        for line in body_lines:
            if not self.add(line):
                break
            added += 1
        return (added, len(body_lines) - added)

    def render(self, fallback_header: str) -> str:
        if not self._lines:
            if self.max_chars <= 1:
                return fallback_header[: self.max_chars]
            return fallback_header[: self.max_chars - 1] + "\n"
        return "\n".join(self._lines) + "\n"


def _summarize_symbols(node: Node, limit: int = 10) -> str:
    exported = [sym.name for sym in node.symbols if sym.exported]
    names = (
        exported
        if exported
        else [sym.name for sym in node.symbols if sym.kind != SymbolKind.METHOD]
    )
    if not names:
        return ""
    if len(names) <= limit:
        return ", ".join(names)
    shown = ", ".join(names[:limit])
    return f"{shown} (+{len(names) - limit} more)"


def _format_edge_targets(
    targets: Sequence[str],
    source_id: str,
    *,
    reverse: bool,
    cycle_edges: set[tuple[str, str]],
    violation_edges: set[tuple[str, str]],
    limit: int = 8,
) -> str:
    if not targets:
        return ""
    formatted: list[str] = []
    for peer in targets[:limit]:
        pair = (peer, source_id) if reverse else (source_id, peer)
        tags: list[str] = []
        if pair in cycle_edges:
            tags.append("CYC")
        if pair in violation_edges:
            tags.append("VIOL")
        suffix = f"[{'/'.join(tags)}]" if tags else ""
        formatted.append(f"{peer}{suffix}")
    if len(targets) > limit:
        formatted.append(f"+{len(targets) - limit} more")
    return ", ".join(formatted)


def _hub_sort_key(node: Node) -> tuple[int, int, int, str]:
    return (-(node.fan_in + node.fan_out), -node.fan_in, -node.loc, node.id)


def _smell_sort_key(smell: ArchSmell) -> tuple[int, str, str]:
    first_edge = smell.edges[0] if smell.edges else ("", "")
    return (_SEVERITY_ORDER[smell.severity], first_edge[0], smell.title)


def _build_smell_lines(result: AnalysisResult, node_by_id: dict[str, Node]) -> list[str]:
    lines: list[str] = []
    for cycle in sorted(result.health.cycles, key=lambda item: (item.id, item.nodes)):
        path_text = cycle.display_path or " -> ".join(cycle.nodes)
        lines.append(f"CYC {cycle.id}: {path_text}")

    violations = sorted(
        (smell for smell in result.health.smells if smell.kind == SmellKind.LAYER_VIOLATION),
        key=_smell_sort_key,
    )
    for smell in violations:
        if smell.edges:
            edge_str = ", ".join(f"{src} -> {dst}" for src, dst in smell.edges)
        else:
            edge_str = ", ".join(smell.nodes)
        lines.append(f"LAY[{smell.severity.value}]: {edge_str}")

    god_nodes = sorted(
        set(result.health.hotspots)
        | {
            node_id
            for smell in result.health.smells
            if smell.kind == SmellKind.GOD_MODULE
            for node_id in smell.nodes
        }
    )
    for node_id in god_nodes:
        node = node_by_id.get(node_id)
        if node is not None:
            lines.append(
                f"GOD: {node_id} (loc={node.loc},cc={node.complexity},"
                f"in={node.fan_in},out={node.fan_out})"
            )
        else:
            lines.append(f"GOD: {node_id}")

    dead_nodes = sorted(
        set(result.health.dead_files)
        | {
            node_id
            for smell in result.health.smells
            if smell.kind == SmellKind.DEAD_CODE
            for node_id in smell.nodes
        }
    )
    for node_id in dead_nodes:
        lines.append(f"DEAD: {node_id}")

    return lines


def _build_route_lines(result: AnalysisResult) -> list[str]:
    route_items: list[tuple[str, RouteInfo]] = []
    nodes_with_routes: set[str] = set()
    for node in sorted(result.graph.nodes, key=lambda item: item.id):
        for route in node.routes:
            route_items.append((node.id, route))
            nodes_with_routes.add(node.id)

    route_items.sort(
        key=lambda item: (item[1].path, item[1].method, item[0], item[1].line, item[1].handler)
    )

    lines: list[str] = []
    for node_id, route in route_items:
        handler = route.handler or "-"
        framework = f" [{route.framework}]" if route.framework else ""
        lines.append(f"{route.method or 'ANY'} {route.path} -> {node_id}:{handler}{framework}")

    for node in sorted(result.graph.nodes, key=lambda item: item.id):
        if node.is_entrypoint and node.id not in nodes_with_routes:
            lines.append(f"ENTRY {node.id} ({node.language.value}, out={node.fan_out})")

    return lines


def export_llms_txt(result: AnalysisResult, *, max_tokens: int = 8000) -> str:
    """Export an ultra-dense, token-budgeted AI codebase map (``llms.txt``)."""
    writer = _BudgetWriter(max_tokens)
    graph = result.graph
    health = result.health
    node_by_id = {node.id: node for node in graph.nodes}

    violations_count = sum(1 for smell in health.smells if smell.kind == SmellKind.LAYER_VIOLATION)
    lang_counts = Counter(node.language.value for node in graph.nodes)
    lang_summary = ", ".join(f"{lang}:{count}" for lang, count in sorted(lang_counts.items()))

    header_line = f"# CODEATLAS {graph.repo_name}"
    writer.add(header_line)
    writer.add(
        f"HEALTH: {health.score:.1f}/100 ({health.grade}) | "
        f"NODES:{len(graph.nodes)} EDGES:{len(graph.edges)} "
        f"CYCLES:{len(health.cycles)} VIOL:{violations_count} "
        f"HOTSPOTS:{len(health.hotspots)} DEAD:{len(health.dead_files)}"
    )
    if lang_summary:
        writer.add(f"LANGS: {lang_summary}")

    budget_exhausted = False

    layer_counts: dict[Layer, int] = {layer: 0 for layer in LAYER_ORDER}
    for node in graph.nodes:
        layer_counts[node.layer] += 1
    for layer, count in health.layer_counts.items():
        if layer_counts[layer] == 0 and count > 0:
            layer_counts[layer] = count
    layer_parts = [
        f"{layer.value}:{layer_counts[layer]}" for layer in LAYER_ORDER if layer_counts[layer] > 0
    ]
    if layer_parts and not budget_exhausted:
        _, omitted = writer.add_section("## LAYERS", [" | ".join(layer_parts)])
        budget_exhausted = omitted > 0

    smell_lines = _build_smell_lines(result, node_by_id)
    if smell_lines and not budget_exhausted:
        _, omitted = writer.add_section("## STRUCTURAL SMELLS", smell_lines)
        budget_exhausted = omitted > 0

    route_lines = _build_route_lines(result)
    if route_lines and not budget_exhausted:
        _, omitted = writer.add_section("## ENTRYPOINTS & ROUTES", route_lines)
        budget_exhausted = omitted > 0

    ranked_nodes = sorted(graph.nodes, key=_hub_sort_key)
    total_nodes = len(ranked_nodes)
    hub_count = total_nodes if total_nodes <= 2 else min(20, max(2, total_nodes // 2))

    hub_nodes = ranked_nodes[:hub_count]
    remaining_nodes = sorted(ranked_nodes[hub_count:], key=lambda item: item.id)

    outgoing: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    incoming: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.source in outgoing and edge.target in incoming:
            outgoing[edge.source].append(edge.target)
            incoming[edge.target].append(edge.source)
    for key in outgoing:
        outgoing[key] = sorted(set(outgoing[key]))
    for key in incoming:
        incoming[key] = sorted(set(incoming[key]))

    cycle_edges = collect_cycle_edges(result)
    violation_edges = collect_violation_edges(result)

    hub_lines: list[str] = []
    for node in hub_nodes:
        degree = node.fan_in + node.fan_out
        hub_lines.append(
            f"HUB {node.id} [{node.layer.value}/{node.language.value}] "
            f"deg={degree}(in={node.fan_in},out={node.fan_out}) "
            f"loc={node.loc} cc={node.complexity}"
        )
        sym_text = _summarize_symbols(node)
        if sym_text:
            hub_lines.append(f"  exports: {sym_text}")
        out_text = _format_edge_targets(
            outgoing.get(node.id, ()),
            node.id,
            reverse=False,
            cycle_edges=cycle_edges,
            violation_edges=violation_edges,
        )
        if out_text:
            hub_lines.append(f"  out: {out_text}")
        in_text = _format_edge_targets(
            incoming.get(node.id, ()),
            node.id,
            reverse=True,
            cycle_edges=cycle_edges,
            violation_edges=violation_edges,
        )
        if in_text:
            hub_lines.append(f"  in: {in_text}")

    if hub_lines and not budget_exhausted:
        _, omitted = writer.add_section("## CENTRAL HUBS", hub_lines)
        budget_exhausted = omitted > 0

    if remaining_nodes and not budget_exhausted:
        rem_lines = [
            f"{node.id} [{node.layer.value}] loc={node.loc} in={node.fan_in} out={node.fan_out}"
            for node in remaining_nodes
        ]
        _, omitted = writer.add_section("## MODULES", rem_lines)
        if omitted > 0:
            writer.add(f"# ... {omitted} additional modules omitted due to token budget")

    return writer.render(header_line)
