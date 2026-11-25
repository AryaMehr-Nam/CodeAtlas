"""Architectural layer boundary violation detection (ARCH-LAY-001)."""

from __future__ import annotations

from codeatlas.models.analysis import ArchSmell, Severity, SmellKind
from codeatlas.models.config import Layer
from codeatlas.models.graph import CodeGraph

LAYER_VIOLATION_RULES: dict[tuple[Layer, Layer], tuple[Severity, str, str]] = {
    (Layer.DATA, Layer.ENTRYPOINT): (
        Severity.HIGH,
        "Data layer modules must not depend on Entrypoint layer modules.",
        "Move orchestration out of the data model/repository or invert via a service callback.",
    ),
    (Layer.DATA, Layer.SERVICE): (
        Severity.HIGH,
        "Data layer modules must not depend on Service layer modules.",
        "Keep data models persistence-focused and move service invocations to the service layer.",
    ),
    (Layer.SHARED, Layer.SERVICE): (
        Severity.HIGH,
        "Shared utility/core modules must not depend on Service layer modules.",
        "Extract pure shared contracts or pass dependencies into the shared helper instead.",
    ),
    (Layer.SHARED, Layer.ENTRYPOINT): (
        Severity.HIGH,
        "Shared utility/core modules must not depend on Entrypoint layer modules.",
        "Remove route/CLI references from shared utilities so shared code stays layer-agnostic.",
    ),
    (Layer.FRONTEND, Layer.DATA): (
        Severity.HIGH,
        "Frontend layer modules must not directly depend on Data layer modules.",
        "Communicate with backend entrypoints/APIs or shared DTO types instead of data models.",
    ),
    (Layer.SHARED, Layer.DATA): (
        Severity.MEDIUM,
        "Shared utility/core modules should not depend on Data layer modules.",
        "Keep shared modules free of persistence coupling or promote the caller to service/data.",
    ),
    (Layer.SERVICE, Layer.ENTRYPOINT): (
        Severity.MEDIUM,
        "Service layer modules should not depend on Entrypoint layer modules.",
        "Entrypoints should call services, not vice versa; move shared types to shared/domain.",
    ),
}

_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.HIGH: 0,
    Severity.MEDIUM: 1,
    Severity.LOW: 2,
}


def detect_layer_violations(
    graph: CodeGraph,
) -> tuple[list[ArchSmell], set[tuple[str, str]]]:
    """Detect ``ARCH-LAY-001`` architectural layer dependency violations.

    Returns ``(smells, violating_edges)`` deterministically sorted by severity
    (high before medium) and ``(source, target)``.
    """
    node_by_id = {node.id: node for node in graph.nodes}
    unique_pairs: set[tuple[str, str]] = set()
    for edge in graph.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in node_by_id or edge.target not in node_by_id:
            continue
        unique_pairs.add((edge.source, edge.target))

    smells: list[ArchSmell] = []
    violating_edges: set[tuple[str, str]] = set()

    for source_id, target_id in sorted(unique_pairs):
        src_node = node_by_id[source_id]
        tgt_node = node_by_id[target_id]
        rule = LAYER_VIOLATION_RULES.get((src_node.layer, tgt_node.layer))
        if rule is None:
            continue

        severity, reason, remediation = rule
        pair = (source_id, target_id)
        violating_edges.add(pair)
        smells.append(
            ArchSmell(
                rule_id="ARCH-LAY-001",
                kind=SmellKind.LAYER_VIOLATION,
                severity=severity,
                title=f"Layer violation: {src_node.layer.value} -> {tgt_node.layer.value}",
                description=(
                    f"{source_id} ({src_node.layer.value}) depends on "
                    f"{target_id} ({tgt_node.layer.value}). {reason}"
                ),
                nodes=[source_id, target_id],
                edges=[pair],
                remediation=remediation,
            )
        )

    smells.sort(
        key=lambda item: (
            _SEVERITY_ORDER[item.severity],
            item.edges[0][0] if item.edges else "",
            item.edges[0][1] if item.edges else "",
        )
    )
    return smells, violating_edges
