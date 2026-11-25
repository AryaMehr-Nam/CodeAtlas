"""Deterministic Mermaid flowchart exporter grouped by architectural layer."""

from __future__ import annotations

import re
from collections.abc import Sequence

from codeatlas.exporters._common import (
    LAYER_LABELS,
    LAYER_ORDER,
    collect_cycle_edges,
    collect_violation_edges,
)
from codeatlas.models.analysis import AnalysisResult
from codeatlas.models.config import Layer
from codeatlas.models.graph import Edge, Node

_NON_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


def _build_mermaid_id_map(nodes: Sequence[Node]) -> dict[str, str]:
    """Build deterministic, collision-free Mermaid node identifiers."""
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for node in sorted(nodes, key=lambda item: item.id):
        cleaned = _NON_IDENT_RE.sub("_", node.id).strip("_")
        base = f"n_{cleaned}" if cleaned else "n_node"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        mapping[node.id] = candidate
    return mapping


def _sanitize_label(text: str) -> str:
    """Escape characters that could break Mermaid quoted node labels."""
    return text.replace('"', "'").replace("\r", " ").replace("\n", " ")


def _edge_sort_key(edge: Edge) -> tuple[str, str, str, tuple[str, ...]]:
    return (edge.source, edge.target, edge.kind, tuple(sorted(edge.symbols)))


def export_mermaid(result: AnalysisResult) -> str:
    """Render a deterministic ``flowchart LR`` Mermaid diagram grouped by layer."""
    nodes_by_layer: dict[Layer, list[Node]] = {layer: [] for layer in LAYER_ORDER}
    for node in sorted(result.graph.nodes, key=lambda item: item.id):
        nodes_by_layer[node.layer].append(node)

    id_map = _build_mermaid_id_map(result.graph.nodes)
    cycle_edges = collect_cycle_edges(result)
    violation_edges = collect_violation_edges(result)

    lines: list[str] = ["flowchart LR"]

    for layer in LAYER_ORDER:
        layer_nodes = nodes_by_layer[layer]
        if not layer_nodes:
            continue
        label = LAYER_LABELS[layer]
        lines.append(f'  subgraph layer_{layer.value}["{label}"]')
        for node in layer_nodes:
            mid = id_map[node.id]
            node_label = _sanitize_label(node.path or node.id)
            lines.append(f'    {mid}["{node_label}"]')
        lines.append("  end")

    link_styles: list[str] = []
    edge_index = 0
    for edge in sorted(result.graph.edges, key=_edge_sort_key):
        if edge.source not in id_map or edge.target not in id_map:
            continue
        src_id = id_map[edge.source]
        dst_id = id_map[edge.target]
        pair = (edge.source, edge.target)
        is_cycle = edge.is_cycle or pair in cycle_edges
        is_violation = edge.is_violation or pair in violation_edges

        if is_cycle and is_violation:
            lines.append(f"  {src_id} -. cycle, violation .-> {dst_id}")
            link_styles.append(
                f"  linkStyle {edge_index} stroke:#e11d48,stroke-width:2.5px,stroke-dasharray:5 5;"
            )
        elif is_cycle:
            lines.append(f"  {src_id} -. cycle .-> {dst_id}")
            link_styles.append(
                f"  linkStyle {edge_index} stroke:#e11d48,stroke-width:2px,stroke-dasharray:5 5;"
            )
        elif is_violation:
            lines.append(f"  {src_id} -. violation .-> {dst_id}")
            link_styles.append(
                f"  linkStyle {edge_index} stroke:#f43f5e,stroke-width:2px,stroke-dasharray:5 5;"
            )
        else:
            lines.append(f"  {src_id} --> {dst_id}")

        edge_index += 1

    lines.extend(link_styles)
    return "\n".join(lines) + "\n"
