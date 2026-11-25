"""Architectural analysis, smell detection, impact, and health report models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from codeatlas.models.config import Layer
from codeatlas.models.graph import CodeGraph, PosixRelPath


class Severity(StrEnum):
    """Severity level for architectural smells."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SmellKind(StrEnum):
    """Categories of architectural smells detected by CodeAtlas."""

    CIRCULAR_DEPENDENCY = "circular_dependency"
    LAYER_VIOLATION = "layer_violation"
    GOD_MODULE = "god_module"
    DEAD_CODE = "dead_code"


class Cycle(BaseModel):
    """A strongly connected dependency cycle among module nodes."""

    id: str
    nodes: list[PosixRelPath] = Field(default_factory=list)
    edges: list[tuple[PosixRelPath, PosixRelPath]] = Field(default_factory=list)
    display_path: str = ""


class ArchSmell(BaseModel):
    """An architectural anti-pattern or rule violation detected in the graph."""

    rule_id: str
    kind: SmellKind
    severity: Severity
    title: str
    description: str
    nodes: list[PosixRelPath] = Field(default_factory=list)
    edges: list[tuple[PosixRelPath, PosixRelPath]] = Field(default_factory=list)
    remediation: str = ""


class ImpactNode(BaseModel):
    """A module affected upstream or downstream from a target module."""

    node_id: PosixRelPath
    depth: int = Field(ge=0)
    path: list[PosixRelPath] = Field(default_factory=list)


class BlastRadius(BaseModel):
    """Upstream callers and downstream dependencies impacted by a target module."""

    target_id: PosixRelPath
    upstream: list[ImpactNode] = Field(default_factory=list)
    downstream: list[ImpactNode] = Field(default_factory=list)
    upstream_count: int = Field(default=0, ge=0)
    downstream_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _sync_counts(self) -> BlastRadius:
        if "upstream_count" not in self.model_fields_set:
            self.upstream_count = len(self.upstream)
        if "downstream_count" not in self.model_fields_set:
            self.downstream_count = len(self.downstream)
        return self


class FlowTrace(BaseModel):
    """Execution/dependency trace from an entrypoint toward data-layer targets."""

    entrypoint_id: PosixRelPath
    data_targets: list[PosixRelPath] = Field(default_factory=list)
    paths: list[list[PosixRelPath]] = Field(default_factory=list)
    highlighted_nodes: list[PosixRelPath] = Field(default_factory=list)
    highlighted_edges: list[tuple[PosixRelPath, PosixRelPath]] = Field(default_factory=list)


class HealthBreakdown(BaseModel):
    """Score deduction breakdown across architectural smell categories."""

    base_score: float = 100.0
    cycle_penalty: float = Field(default=0.0, ge=0.0)
    violation_penalty: float = Field(default=0.0, ge=0.0)
    god_penalty: float = Field(default=0.0, ge=0.0)
    dead_penalty: float = Field(default=0.0, ge=0.0)


class HealthReport(BaseModel):
    """Summary health scorecard and detected architectural issues."""

    score: float = Field(default=100.0, ge=0.0, le=100.0)
    grade: str = "A"
    layer_counts: dict[Layer, int] = Field(default_factory=dict)
    cycles: list[Cycle] = Field(default_factory=list)
    smells: list[ArchSmell] = Field(default_factory=list)
    hotspots: list[PosixRelPath] = Field(default_factory=list)
    dead_files: list[PosixRelPath] = Field(default_factory=list)
    breakdown: HealthBreakdown = Field(default_factory=HealthBreakdown)


class AnalysisResult(BaseModel):
    """Combined dependency graph and architectural health analysis output."""

    graph: CodeGraph
    health: HealthReport
