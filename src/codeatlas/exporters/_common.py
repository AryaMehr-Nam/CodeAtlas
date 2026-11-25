"""Shared ordering, label, and text-normalization helpers for CodeAtlas exporters."""

from __future__ import annotations

from codeatlas.models.analysis import AnalysisResult, SmellKind
from codeatlas.models.config import Layer

LAYER_ORDER: tuple[Layer, ...] = (
    Layer.FRONTEND,
    Layer.ENTRYPOINT,
    Layer.SERVICE,
    Layer.DATA,
    Layer.SHARED,
    Layer.CONFIG,
    Layer.TEST,
)

LAYER_LABELS: dict[Layer, str] = {
    Layer.FRONTEND: "Frontend",
    Layer.ENTRYPOINT: "Entrypoint",
    Layer.SERVICE: "Service",
    Layer.DATA: "Data",
    Layer.SHARED: "Shared",
    Layer.CONFIG: "Config",
    Layer.TEST: "Test",
}


def normalize_newlines(text: str) -> str:
    """Normalize CRLF and lone CR line endings to LF (``\\n``)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def collect_cycle_edges(result: AnalysisResult) -> set[tuple[str, str]]:
    """Return all directed ``(source, target)`` pairs participating in detected cycles."""
    pairs: set[tuple[str, str]] = {
        (edge.source, edge.target) for edge in result.graph.edges if edge.is_cycle
    }
    for cycle in result.health.cycles:
        for src, dst in cycle.edges:
            pairs.add((src, dst))
    return pairs


def collect_violation_edges(result: AnalysisResult) -> set[tuple[str, str]]:
    """Return all directed ``(source, target)`` pairs participating in layer violations."""
    pairs: set[tuple[str, str]] = {
        (edge.source, edge.target) for edge in result.graph.edges if edge.is_violation
    }
    for smell in result.health.smells:
        if smell.kind == SmellKind.LAYER_VIOLATION:
            for src, dst in smell.edges:
                pairs.add((src, dst))
    return pairs
