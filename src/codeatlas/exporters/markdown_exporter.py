"""Markdown (ARCHITECTURE.md) exporter for CodeAtlas analysis results."""

from __future__ import annotations

from codeatlas.exporters._common import LAYER_LABELS, LAYER_ORDER
from codeatlas.exporters.mermaid_exporter import export_mermaid
from codeatlas.models.analysis import AnalysisResult, ArchSmell, Severity, SmellKind
from codeatlas.models.config import Layer
from codeatlas.models.graph import Node, RouteInfo

_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.HIGH: 0,
    Severity.MEDIUM: 1,
    Severity.LOW: 2,
}


def _md_cell(value: str) -> str:
    """Escape Markdown table delimiters and line breaks inside cell values."""
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _sort_violation_key(smell: ArchSmell) -> tuple[int, str, str, str]:
    first_edge = smell.edges[0] if smell.edges else ("", "")
    return (_SEVERITY_ORDER[smell.severity], first_edge[0], first_edge[1], smell.title)


def _render_health_section(result: AnalysisResult, violations: list[ArchSmell]) -> list[str]:
    health = result.health
    breakdown = health.breakdown
    lines = [
        "## Health Score Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Health Score | **{health.score:.1f} / 100** |",
        f"| Grade | **{health.grade}** |",
        f"| Modules | {len(result.graph.nodes)} |",
        f"| Dependencies | {len(result.graph.edges)} |",
        f"| Circular Dependencies | {len(health.cycles)} |",
        f"| Layer Violations | {len(violations)} |",
        f"| God Module Hotspots | {len(health.hotspots)} |",
        f"| Dead-Code Candidates | {len(health.dead_files)} |",
        "",
        "### Score Breakdown",
        "",
        "| Category | Points |",
        "| --- | --- |",
        f"| Base Score | {breakdown.base_score:.1f} |",
        f"| Circular Dependency Penalty | -{breakdown.cycle_penalty:.1f} |",
        f"| Layer Violation Penalty | -{breakdown.violation_penalty:.1f} |",
        f"| God Module Penalty | -{breakdown.god_penalty:.1f} |",
        f"| Dead Code Penalty | -{breakdown.dead_penalty:.1f} |",
        f"| **Final Score** | **{health.score:.1f}** |",
        "",
    ]
    return lines


def _render_layer_breakdown(result: AnalysisResult) -> list[str]:
    nodes_by_layer: dict[Layer, list[Node]] = {layer: [] for layer in LAYER_ORDER}
    for node in result.graph.nodes:
        nodes_by_layer[node.layer].append(node)

    rows: list[str] = []
    for layer in LAYER_ORDER:
        layer_nodes = nodes_by_layer[layer]
        reported_count = result.health.layer_counts.get(layer, 0)
        module_count = len(layer_nodes) if layer_nodes else reported_count
        if module_count == 0:
            continue
        total_loc = sum(node.loc for node in layer_nodes)
        avg_complexity = (
            sum(node.complexity for node in layer_nodes) / len(layer_nodes) if layer_nodes else 0.0
        )
        route_count = sum(len(node.routes) for node in layer_nodes)
        rows.append(
            f"| {LAYER_LABELS[layer]} | {module_count} | {total_loc} | "
            f"{avg_complexity:.1f} | {route_count} |"
        )

    lines = ["## Layer Breakdown", ""]
    if not rows:
        lines.extend(["_No modules analyzed._", ""])
        return lines

    lines.extend(
        [
            "| Layer | Modules | Total LOC | Avg Complexity | Routes |",
            "| --- | ---: | ---: | ---: | ---: |",
            *rows,
            "",
        ]
    )
    return lines


def _render_routes_section(result: AnalysisResult) -> list[str]:
    route_entries: list[tuple[str, RouteInfo]] = []
    for node in sorted(result.graph.nodes, key=lambda item: item.id):
        for route in node.routes:
            route_entries.append((node.id, route))

    route_entries.sort(
        key=lambda entry: (
            entry[1].path,
            entry[1].method,
            entry[0],
            entry[1].line,
            entry[1].handler,
        )
    )

    lines = ["## API Route Inventory", ""]
    if not route_entries:
        lines.extend(["_No HTTP or API routes detected._", ""])
        return lines

    lines.extend(
        [
            "| Method | Path | Handler | Framework | Module |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for node_id, route in route_entries:
        method = _md_cell(route.method or "ANY")
        path = _md_cell(route.path)
        handler = _md_cell(route.handler or "-")
        framework = _md_cell(route.framework or "-")
        module = _md_cell(node_id)
        lines.append(f"| `{method}` | `{path}` | `{handler}` | {framework} | `{module}` |")
    lines.append("")
    return lines


def _render_hotspots_section(
    result: AnalysisResult,
    node_by_id: dict[str, Node],
) -> list[str]:
    god_smells: dict[str, ArchSmell] = {}
    for smell in result.health.smells:
        if smell.kind == SmellKind.GOD_MODULE:
            for node_id in smell.nodes:
                god_smells.setdefault(node_id, smell)

    hotspot_ids = sorted(set(result.health.hotspots) | set(god_smells.keys()))
    lines = ["## Hotspots", ""]
    if not hotspot_ids:
        lines.extend(["_No God module hotspots detected._", ""])
        return lines

    lines.extend(
        [
            "| Module | Layer | LOC | Complexity | Fan-In | Fan-Out | Instability | Notes |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for node_id in hotspot_ids:
        node = node_by_id.get(node_id)
        hotspot_smell = god_smells.get(node_id)
        note = _md_cell(
            hotspot_smell.description if hotspot_smell else "Flagged as architectural hotspot."
        )
        if node is not None:
            lines.append(
                f"| `{_md_cell(node_id)}` | {LAYER_LABELS[node.layer]} | {node.loc} | "
                f"{node.complexity} | {node.fan_in} | {node.fan_out} | "
                f"{node.instability:.2f} | {note} |"
            )
        else:
            lines.append(f"| `{_md_cell(node_id)}` | - | - | - | - | - | - | {note} |")
    lines.append("")
    return lines


def _render_cycles_section(result: AnalysisResult) -> list[str]:
    cycles = sorted(result.health.cycles, key=lambda item: (item.id, item.nodes))
    lines = ["## Circular Dependencies", ""]
    if not cycles:
        lines.extend(["_No circular dependencies detected._", ""])
        return lines

    for cycle in cycles:
        path_text = cycle.display_path or " -> ".join(cycle.nodes)
        lines.append(
            f"- **{cycle.id}** ({len(cycle.nodes)} modules, {len(cycle.edges)} edges): "
            f"`{path_text}`"
        )
    lines.append("")
    return lines


def _render_violations_section(violations: list[ArchSmell]) -> list[str]:
    lines = ["## Layer Violations", ""]
    if not violations:
        lines.extend(["_No architectural layer violations detected._", ""])
        return lines

    for smell in violations:
        lines.append(
            f"- **[{smell.severity.value.upper()}] {smell.title}** (`{smell.rule_id}`): "
            f"{smell.description}"
        )
        if smell.remediation:
            lines.append(f"  - Remediation: {smell.remediation}")
    lines.append("")
    return lines


def _render_dead_code_section(
    result: AnalysisResult,
    node_by_id: dict[str, Node],
) -> list[str]:
    dead_smells: dict[str, ArchSmell] = {}
    for smell in result.health.smells:
        if smell.kind == SmellKind.DEAD_CODE:
            for node_id in smell.nodes:
                dead_smells.setdefault(node_id, smell)

    dead_ids = sorted(set(result.health.dead_files) | set(dead_smells.keys()))
    lines = ["## Dead-Code Candidates", ""]
    if not dead_ids:
        lines.extend(["_No dead-code candidates detected._", ""])
        return lines

    for node_id in dead_ids:
        node = node_by_id.get(node_id)
        detail = (
            f"{LAYER_LABELS[node.layer]}, {node.loc} LOC, fan-in=0"
            if node is not None
            else "fan-in=0"
        )
        lines.append(f"- `{node_id}` ({detail})")
    lines.append("")
    return lines


def _render_warnings_section(result: AnalysisResult) -> list[str]:
    if not result.graph.warnings:
        return []
    lines = ["## Parse Warnings", ""]
    for warning in sorted(result.graph.warnings, key=lambda item: (item.path, item.message)):
        fallback = f" (fallback: `{warning.fallback_used}`)" if warning.fallback_used else ""
        lines.append(f"- `{warning.path}`: {warning.message}{fallback}")
    lines.append("")
    return lines


def export_markdown(result: AnalysisResult) -> str:
    """Render a deterministic ``ARCHITECTURE.md`` report from an ``AnalysisResult``."""
    node_by_id = {node.id: node for node in result.graph.nodes}
    violations = sorted(
        (smell for smell in result.health.smells if smell.kind == SmellKind.LAYER_VIOLATION),
        key=_sort_violation_key,
    )
    mermaid_diagram = export_mermaid(result).rstrip("\n")

    lines: list[str] = [
        f"# Architecture Report: {result.graph.repo_name}",
        "",
        f"- **Root Path**: `{result.graph.root_path}`",
        f"- **Modules**: {len(result.graph.nodes)} | **Dependencies**: {len(result.graph.edges)}",
    ]
    if result.graph.truncated:
        lines.append(
            f"- **Note**: Scan truncated (`total_files_seen={result.graph.total_files_seen}`)."
        )
    lines.append("")

    lines.extend(_render_health_section(result, violations))
    lines.extend(_render_layer_breakdown(result))
    lines.extend(
        [
            "## Dependency Graph",
            "",
            "```mermaid",
            mermaid_diagram,
            "```",
            "",
        ]
    )
    lines.extend(_render_routes_section(result))
    lines.extend(_render_hotspots_section(result, node_by_id))
    lines.extend(_render_cycles_section(result))
    lines.extend(_render_violations_section(violations))
    lines.extend(_render_dead_code_section(result, node_by_id))
    lines.extend(_render_warnings_section(result))

    return "\n".join(lines).rstrip("\n") + "\n"
