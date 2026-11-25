"""End-to-end repository scanning and architectural analysis orchestrator."""

from __future__ import annotations

import posixpath
from collections import Counter
from pathlib import Path

from codeatlas.analysis.cycles import cycles_to_smells, detect_cycles
from codeatlas.analysis.layers import classify_layer
from codeatlas.analysis.metrics import analyze_metrics
from codeatlas.analysis.violations import detect_layer_violations
from codeatlas.models.analysis import (
    AnalysisResult,
    ArchSmell,
    HealthBreakdown,
    HealthReport,
    Severity,
)
from codeatlas.models.config import Layer, ScanConfig
from codeatlas.models.graph import (
    Cluster,
    CodeGraph,
    Node,
    ParseWarning,
    normalize_posix_path,
)
from codeatlas.scanner.extractors import FileExtraction, extract_file
from codeatlas.scanner.parser_registry import ParserRegistry
from codeatlas.scanner.resolver import resolve_dependencies
from codeatlas.scanner.walker import walk_repository

_CONTAINER_ROOTS: frozenset[str] = frozenset({"src", "lib", "app"})
_MONOREPO_ROOTS: frozenset[str] = frozenset({"apps", "packages", "services", "modules", "libs"})

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.HIGH: 0,
    Severity.MEDIUM: 1,
    Severity.LOW: 2,
}


def _cluster_directory_for_node(
    directory: str,
    single_pkg_under_container: dict[str, bool],
) -> str:
    """Choose a meaningful cluster directory key for a node's POSIX directory."""
    if not directory or directory == ".":
        return "."

    parts = [part for part in directory.split("/") if part and part != "."]
    if not parts:
        return "."

    first = parts[0]
    if first in _MONOREPO_ROOTS and len(parts) >= 2:
        return "/".join(parts[:2])

    if first in _CONTAINER_ROOTS:
        if len(parts) >= 3 and single_pkg_under_container.get(first, False):
            return "/".join(parts[:3])
        if len(parts) >= 2:
            return "/".join(parts[:2])
        return first

    return first


def _build_clusters(nodes: list[Node]) -> list[Cluster]:
    """Group nodes deterministically into directory clusters and assign ``node.cluster``."""
    subpkgs_by_container: dict[str, set[str]] = {root: set() for root in _CONTAINER_ROOTS}
    for node in nodes:
        if node.directory and node.directory != ".":
            parts = [part for part in node.directory.split("/") if part and part != "."]
            if len(parts) >= 2 and parts[0] in _CONTAINER_ROOTS:
                subpkgs_by_container[parts[0]].add(parts[1])

    single_pkg_under_container = {
        root: len(pkgs) == 1 for root, pkgs in subpkgs_by_container.items()
    }

    grouped: dict[str, list[Node]] = {}
    for node in nodes:
        cluster_dir = _cluster_directory_for_node(node.directory, single_pkg_under_container)
        grouped.setdefault(cluster_dir, []).append(node)

    clusters: list[Cluster] = []
    for cluster_dir in sorted(grouped.keys()):
        group_nodes = sorted(grouped[cluster_dir], key=lambda item: item.id)
        cluster_id = "root" if cluster_dir == "." else cluster_dir
        layer_counts = Counter(item.layer for item in group_nodes)
        primary_layer = min(
            layer_counts.keys(),
            key=lambda layer: (-layer_counts[layer], layer.value),
        )
        for item in group_nodes:
            item.cluster = cluster_id

        clusters.append(
            Cluster(
                id=cluster_id,
                label=cluster_id,
                directory=cluster_dir,
                node_ids=[item.id for item in group_nodes],
                primary_layer=primary_layer,
            )
        )

    return clusters


def _score_to_grade(score: float) -> str:
    """Convert numeric 0-100 health score into a letter grade (A+..F)."""
    if score >= 95.0:
        return "A+"
    if score >= 90.0:
        return "A"
    if score >= 85.0:
        return "A-"
    if score >= 80.0:
        return "B+"
    if score >= 75.0:
        return "B"
    if score >= 70.0:
        return "B-"
    if score >= 65.0:
        return "C+"
    if score >= 60.0:
        return "C"
    if score >= 50.0:
        return "D"
    return "F"


def _compute_health_report(
    nodes: list[Node],
    cycles_list: list[ArchSmell],
    violation_smells: list[ArchSmell],
    all_smells: list[ArchSmell],
    hotspots: list[str],
    dead_files: list[str],
    raw_cycles: list[tuple[int, int]] | None = None,
) -> tuple[float, str, HealthBreakdown, dict[Layer, int]]:
    """Compute transparent health score, grade, breakdown, and layer counts."""
    del cycles_list
    cycle_specs = raw_cycles or []
    cycle_penalty = round(
        min(35.0, sum(8.0 + 2.0 * max(node_count - 2, 0) for node_count, _ in cycle_specs)),
        1,
    )
    violation_penalty = round(
        min(
            30.0,
            sum(7.0 if smell.severity == Severity.HIGH else 3.5 for smell in violation_smells),
        ),
        1,
    )
    god_penalty = round(min(20.0, 5.0 * len(hotspots)), 1)
    dead_penalty = round(min(15.0, 2.0 * len(dead_files)), 1)

    breakdown = HealthBreakdown(
        base_score=100.0,
        cycle_penalty=cycle_penalty,
        violation_penalty=violation_penalty,
        god_penalty=god_penalty,
        dead_penalty=dead_penalty,
    )
    raw_score = 100.0 - cycle_penalty - violation_penalty - god_penalty - dead_penalty
    score = round(max(0.0, min(100.0, raw_score)), 1)
    grade = _score_to_grade(score)

    raw_layer_counts = Counter(node.layer for node in nodes)
    layer_counts: dict[Layer, int] = {
        layer: raw_layer_counts[layer] for layer in Layer if raw_layer_counts[layer] > 0
    }
    del all_smells
    return score, grade, breakdown, layer_counts


def scan_repository(root_path: str | Path, config: ScanConfig | None = None) -> AnalysisResult:
    """Scan a repository end-to-end and return its dependency graph and health report."""
    cfg = config or ScanConfig()
    resolved_root = Path(root_path).resolve()
    walk_result = walk_repository(resolved_root, cfg, supported_only=True)
    registry = ParserRegistry()

    extractions: dict[str, FileExtraction] = {}
    warnings: list[ParseWarning] = list(walk_result.warnings)

    for walked_file in walk_result.files:
        extraction = extract_file(walked_file, registry)
        extractions[extraction.path] = extraction
        warnings.extend(extraction.warnings)

    resolved = resolve_dependencies(resolved_root, extractions)

    nodes: list[Node] = []
    for rel_path in sorted(extractions.keys()):
        ext = extractions[rel_path]
        layer = classify_layer(
            rel_path,
            ext.language,
            is_entrypoint=ext.is_entrypoint,
            has_routes=bool(ext.routes),
            has_db_access=ext.has_db_access,
        )
        directory = posixpath.dirname(rel_path) or "."
        name = posixpath.basename(rel_path)
        sorted_symbols = sorted(
            ext.symbols,
            key=lambda sym: (sym.line, sym.end_line, sym.name, sym.kind.value),
        )
        sorted_routes = sorted(
            ext.routes,
            key=lambda route: (route.line, route.method, route.path, route.handler),
        )
        external_deps = sorted(set(resolved.external_deps_by_node.get(rel_path, [])))
        nodes.append(
            Node(
                id=rel_path,
                path=rel_path,
                name=name,
                directory=directory,
                language=ext.language,
                layer=layer,
                loc=ext.loc,
                code_lines=ext.code_lines,
                comment_lines=ext.comment_lines,
                complexity=ext.complexity,
                is_entrypoint=ext.is_entrypoint,
                has_db_access=ext.has_db_access,
                symbols=sorted_symbols,
                routes=sorted_routes,
                external_deps=external_deps,
            )
        )

    clusters = _build_clusters(nodes)
    edges = sorted(resolved.internal_edges, key=lambda edge: (edge.source, edge.target))
    sorted_warnings = sorted(
        warnings,
        key=lambda warn: (warn.path, warn.message, warn.fallback_used or ""),
    )

    repo_name = resolved_root.name or str(resolved_root)
    graph = CodeGraph(
        repo_name=repo_name,
        root_path=normalize_posix_path(str(resolved_root)),
        truncated=walk_result.truncated,
        total_files_seen=walk_result.total_files_seen,
        nodes=nodes,
        edges=edges,
        clusters=clusters,
        warnings=sorted_warnings,
    )

    metrics_summary = analyze_metrics(graph, thresholds=cfg.thresholds)
    cycles, cycle_edges = detect_cycles(graph)
    cycle_smells = cycles_to_smells(cycles)
    violation_smells, violating_edges = detect_layer_violations(graph)

    for edge in graph.edges:
        pair = (edge.source, edge.target)
        edge.is_cycle = pair in cycle_edges
        edge.is_violation = pair in violating_edges

    cycle_nodes = {node_id for cycle in cycles for node_id in cycle.nodes}
    high_violation_nodes = {
        node_id
        for smell in violation_smells
        if smell.severity == Severity.HIGH
        for node_id in smell.nodes
    }
    medium_violation_nodes = {
        node_id
        for smell in violation_smells
        if smell.severity == Severity.MEDIUM
        for node_id in smell.nodes
    }
    hotspot_set = set(metrics_summary.hotspots)
    dead_set = set(metrics_summary.dead_files)

    for node in graph.nodes:
        if node.id in cycle_nodes or node.id in high_violation_nodes:
            node.health_status = "critical"
        elif node.id in hotspot_set or node.id in dead_set or node.id in medium_violation_nodes:
            node.health_status = "warning"
        else:
            node.health_status = "healthy"

    all_smells = [*cycle_smells, *violation_smells, *metrics_summary.smells]
    all_smells.sort(
        key=lambda smell: (
            _SEVERITY_RANK[smell.severity],
            smell.rule_id,
            tuple(smell.nodes),
            tuple(smell.edges),
            smell.title,
        )
    )

    score, grade, breakdown, layer_counts = _compute_health_report(
        nodes=graph.nodes,
        cycles_list=cycle_smells,
        violation_smells=violation_smells,
        all_smells=all_smells,
        hotspots=metrics_summary.hotspots,
        dead_files=metrics_summary.dead_files,
        raw_cycles=[(len(cycle.nodes), len(cycle.edges)) for cycle in cycles],
    )

    language_counts = Counter(node.language.value for node in graph.nodes)
    graph.stats = {
        "cycle_count": len(cycles),
        "dead_file_count": len(metrics_summary.dead_files),
        "hotspot_count": len(metrics_summary.hotspots),
        "languages": {lang: language_counts[lang] for lang in sorted(language_counts.keys())},
        "total_code_lines": sum(node.code_lines for node in graph.nodes),
        "total_comment_lines": sum(node.comment_lines for node in graph.nodes),
        "total_edges": len(graph.edges),
        "total_loc": sum(node.loc for node in graph.nodes),
        "total_nodes": len(graph.nodes),
        "violation_count": len(violation_smells),
    }

    health = HealthReport(
        score=score,
        grade=grade,
        layer_counts=layer_counts,
        cycles=cycles,
        smells=all_smells,
        hotspots=sorted(metrics_summary.hotspots),
        dead_files=sorted(metrics_summary.dead_files),
        breakdown=breakdown,
    )

    return AnalysisResult(graph=graph, health=health)
