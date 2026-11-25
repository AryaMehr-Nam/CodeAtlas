"""Graph coupling metrics, God Module hotspot detection, and dead-code analysis."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from codeatlas.models.analysis import ArchSmell, Severity, SmellKind
from codeatlas.models.config import AnalysisThresholds, Layer
from codeatlas.models.graph import CodeGraph, Node, normalize_posix_rel_path

_PACKAGE_MARKERS: frozenset[str] = frozenset(
    {
        "__init__.py",
        "__main__.py",
        "index.ts",
        "index.tsx",
        "index.js",
        "index.jsx",
        "index.mjs",
        "index.cjs",
    }
)

_FRONTEND_PAGE_DIRS: frozenset[str] = frozenset({"pages", "page", "views", "screens"})

_FRONTEND_ROOT_STEMS: frozenset[str] = frozenset(
    {"main", "app", "index", "root", "_app", "_document"}
)

_NEXTJS_CONVENTION_STEMS: frozenset[str] = frozenset(
    {"page", "layout", "loading", "error", "not-found", "template", "default", "route"}
)

_JS_TS_EXTS: frozenset[str] = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})
_FRONTEND_EXTS: frozenset[str] = frozenset({".tsx", ".jsx", ".vue", ".svelte", ".astro"})


@dataclass(frozen=True, slots=True)
class MetricsAnalysis:
    """Combined coupling metrics, God Module hotspots, and dead-file findings."""

    smells: list[ArchSmell]
    hotspots: list[str]
    dead_files: list[str]


def compute_node_metrics(graph: CodeGraph) -> None:
    """Compute ``fan_in``, ``fan_out``, and ``instability`` for each node in-place.

    Duplicate directed edges and self-loops are excluded when counting fan-in
    and fan-out. Isolated nodes (``fan_in + fan_out == 0``) receive ``0.0``.
    """
    node_ids = {node.id for node in graph.nodes}
    fan_in_counts: dict[str, int] = dict.fromkeys(node_ids, 0)
    fan_out_counts: dict[str, int] = dict.fromkeys(node_ids, 0)

    seen_edges: set[tuple[str, str]] = set()
    for edge in graph.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in node_ids or edge.target not in node_ids:
            continue
        pair = (edge.source, edge.target)
        if pair in seen_edges:
            continue
        seen_edges.add(pair)
        fan_out_counts[edge.source] += 1
        fan_in_counts[edge.target] += 1

    for node in graph.nodes:
        fan_in = fan_in_counts[node.id]
        fan_out = fan_out_counts[node.id]
        total = fan_in + fan_out
        node.fan_in = fan_in
        node.fan_out = fan_out
        node.instability = round(fan_out / total, 4) if total > 0 else 0.0


def is_exempt_from_dead_code(node: Node) -> bool:
    """Return True when a module with ``fan_in == 0`` is legitimately unimported."""
    if node.layer in {Layer.ENTRYPOINT, Layer.TEST, Layer.CONFIG}:
        return True
    if node.is_entrypoint or node.routes:
        return True

    normalized = normalize_posix_rel_path(node.path or node.id)
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return True

    dirs_lower = {part.lower() for part in parts[:-1]}
    filename = parts[-1]
    filename_lower = filename.lower()

    if filename in {"__init__.py", "__main__.py"} or filename_lower in _PACKAGE_MARKERS:
        return True

    stem, ext = posixpath.splitext(filename)
    stem_lower = stem.lower()
    ext_lower = ext.lower()

    is_frontend = node.layer == Layer.FRONTEND or ext_lower in _FRONTEND_EXTS
    if dirs_lower & _FRONTEND_PAGE_DIRS:
        return True

    if is_frontend:
        if stem_lower in _FRONTEND_ROOT_STEMS:
            return True
        if "app" in dirs_lower and stem_lower in _NEXTJS_CONVENTION_STEMS:
            return True

    return ext_lower in _JS_TS_EXTS and stem_lower in {"main", "app", "index"}


def detect_god_modules(
    graph: CodeGraph,
    thresholds: AnalysisThresholds | None = None,
) -> tuple[list[ArchSmell], list[str]]:
    """Detect ``ARCH-GOD-001`` (God Module Hotspot) smells across non-test/config nodes."""
    active_thresholds = thresholds or AnalysisThresholds()
    smells: list[ArchSmell] = []
    hotspots: list[str] = []

    for node in sorted(graph.nodes, key=lambda item: item.id):
        if node.layer in {Layer.TEST, Layer.CONFIG}:
            continue

        triggers: list[str] = []
        if node.loc >= active_thresholds.god_loc:
            triggers.append(f"loc={node.loc} (>= {active_thresholds.god_loc})")
        if node.complexity >= active_thresholds.god_complexity:
            triggers.append(f"complexity={node.complexity} (>= {active_thresholds.god_complexity})")
        if node.fan_out >= active_thresholds.god_fan_out:
            triggers.append(f"fan_out={node.fan_out} (>= {active_thresholds.god_fan_out})")
        if node.fan_in >= active_thresholds.god_fan_in:
            triggers.append(f"fan_in={node.fan_in} (>= {active_thresholds.god_fan_in})")

        if not triggers:
            continue

        hotspots.append(node.id)
        smells.append(
            ArchSmell(
                rule_id="ARCH-GOD-001",
                kind=SmellKind.GOD_MODULE,
                severity=Severity.MEDIUM,
                title=f"God module hotspot: {node.name or node.id}",
                description=(f"{node.id} exceeds God Module thresholds: {', '.join(triggers)}."),
                nodes=[node.id],
                edges=[],
                remediation=(
                    "Split overloaded responsibilities into smaller, focused modules with "
                    "clearer single-purpose boundaries."
                ),
            )
        )

    return smells, hotspots


def detect_dead_files(graph: CodeGraph) -> tuple[list[ArchSmell], list[str]]:
    """Detect ``ARCH-DEAD-001`` (Orphan / Potentially Dead File) smells."""
    smells: list[ArchSmell] = []
    dead_files: list[str] = []

    for node in sorted(graph.nodes, key=lambda item: item.id):
        if node.fan_in != 0 or is_exempt_from_dead_code(node):
            continue

        dead_files.append(node.id)
        smells.append(
            ArchSmell(
                rule_id="ARCH-DEAD-001",
                kind=SmellKind.DEAD_CODE,
                severity=Severity.LOW,
                title=f"Potentially dead module: {node.name or node.id}",
                description=(
                    f"{node.id} ({node.layer.value}) has fan_in=0, no discovered routes, and "
                    "is not an entrypoint, test, config, frontend page, or package marker."
                ),
                nodes=[node.id],
                edges=[],
                remediation=(
                    "Verify whether this module is dynamically loaded or remove it if obsolete."
                ),
            )
        )

    return smells, dead_files


def analyze_metrics(
    graph: CodeGraph,
    thresholds: AnalysisThresholds | None = None,
) -> MetricsAnalysis:
    """Compute node coupling metrics and detect God Module and dead-file smells."""
    compute_node_metrics(graph)
    god_smells, hotspots = detect_god_modules(graph, thresholds=thresholds)
    dead_smells, dead_files = detect_dead_files(graph)
    return MetricsAnalysis(
        smells=[*god_smells, *dead_smells],
        hotspots=hotspots,
        dead_files=dead_files,
    )
