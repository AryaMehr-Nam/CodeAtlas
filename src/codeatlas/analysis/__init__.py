"""Architecture Intelligence Engine analysis modules."""

from __future__ import annotations

from codeatlas.analysis.blast_radius import compute_blast_radius, trace_request_to_db
from codeatlas.analysis.cycles import (
    cycles_to_smells,
    detect_cycles,
    find_shortest_cycle_path,
    find_strongly_connected_components,
)
from codeatlas.analysis.engine import scan_repository
from codeatlas.analysis.layers import classify_layer
from codeatlas.analysis.metrics import (
    MetricsAnalysis,
    analyze_metrics,
    compute_node_metrics,
    detect_dead_files,
    detect_god_modules,
    is_exempt_from_dead_code,
)
from codeatlas.analysis.violations import LAYER_VIOLATION_RULES, detect_layer_violations

__all__ = [
    "LAYER_VIOLATION_RULES",
    "MetricsAnalysis",
    "analyze_metrics",
    "classify_layer",
    "compute_blast_radius",
    "compute_node_metrics",
    "cycles_to_smells",
    "detect_cycles",
    "detect_dead_files",
    "detect_god_modules",
    "detect_layer_violations",
    "find_shortest_cycle_path",
    "find_strongly_connected_components",
    "is_exempt_from_dead_code",
    "scan_repository",
    "trace_request_to_db",
]
