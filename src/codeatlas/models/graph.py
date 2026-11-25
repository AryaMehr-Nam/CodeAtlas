"""Dependency graph models for parsed modules, symbols, routes, and clusters."""

from __future__ import annotations

import posixpath
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from codeatlas.models.config import Language, Layer


def normalize_posix_path(value: str) -> str:
    """Normalize any path separators to POSIX forward slashes."""
    cleaned = value.strip().replace("\\", "/")
    if not cleaned:
        return ""
    if len(cleaned) == 3 and cleaned[1:] == ":/" and cleaned[0].isalpha():
        return cleaned
    return posixpath.normpath(cleaned)


def normalize_posix_rel_path(value: str) -> str:
    """Normalize a repository-relative path or node ID to POSIX forward slashes."""
    cleaned = value.strip().replace("\\", "/")
    if not cleaned:
        return ""
    stripped = cleaned.lstrip("/")
    if not stripped:
        return ""
    return posixpath.normpath(stripped)


PosixPathStr = Annotated[str, AfterValidator(normalize_posix_path)]
PosixRelPath = Annotated[str, AfterValidator(normalize_posix_rel_path)]


class SymbolKind(StrEnum):
    """Kinds of top-level or member symbols extracted from source files."""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    TYPE = "type"
    STRUCT = "struct"
    TRAIT = "trait"
    ENUM = "enum"
    CONSTANT = "constant"
    VARIABLE = "variable"
    COMPONENT = "component"
    UNKNOWN = "unknown"


class Symbol(BaseModel):
    """A code symbol defined inside a source module."""

    name: str
    kind: SymbolKind
    line: int = Field(ge=1)
    end_line: int = Field(default=0, ge=0)
    exported: bool = False
    signature: str | None = None

    @model_validator(mode="after")
    def _normalize_end_line(self) -> Symbol:
        if self.end_line == 0:
            self.end_line = self.line
        elif self.end_line < self.line:
            raise ValueError(f"end_line ({self.end_line}) must be >= line ({self.line})")
        return self


class RouteInfo(BaseModel):
    """An HTTP or framework route discovered inside a module."""

    method: str
    path: str
    handler: str = ""
    line: int = Field(default=1, ge=1)
    framework: str = ""

    @field_validator("method")
    @classmethod
    def _normalize_method(cls, value: str) -> str:
        return value.strip().upper()


class ImportRef(BaseModel):
    """A raw import or dependency reference extracted from a source file."""

    raw: str
    names: list[str] = Field(default_factory=list)
    is_relative: bool = False
    relative_level: int = Field(default=0, ge=0)
    line: int = Field(default=1, ge=1)


class ParseWarning(BaseModel):
    """A non-fatal warning recorded while walking or parsing a file."""

    path: PosixRelPath
    message: str
    fallback_used: str | None = None


class Node(BaseModel):
    """A module node in the repository architecture graph."""

    id: PosixRelPath = ""
    path: PosixRelPath
    name: str = ""
    directory: PosixRelPath = ""
    language: Language
    layer: Layer = Layer.SHARED
    cluster: str = ""
    loc: int = Field(default=0, ge=0)
    code_lines: int = Field(default=0, ge=0)
    comment_lines: int = Field(default=0, ge=0)
    complexity: int = Field(default=1, ge=0)
    fan_in: int = Field(default=0, ge=0)
    fan_out: int = Field(default=0, ge=0)
    instability: float = Field(default=0.0, ge=0.0, le=1.0)
    is_entrypoint: bool = False
    has_db_access: bool = False
    symbols: list[Symbol] = Field(default_factory=list)
    routes: list[RouteInfo] = Field(default_factory=list)
    external_deps: list[str] = Field(default_factory=list)
    health_status: str = "healthy"

    @model_validator(mode="after")
    def _populate_defaults(self) -> Node:
        if not self.id:
            self.id = self.path
        if not self.name and self.path:
            self.name = posixpath.basename(self.path)
        if "directory" not in self.model_fields_set and self.path:
            self.directory = posixpath.dirname(self.path) or "."
        return self


class Edge(BaseModel):
    """A directed dependency edge between two module nodes."""

    source: PosixRelPath
    target: PosixRelPath
    symbols: list[str] = Field(default_factory=list)
    kind: str = "import"
    is_cycle: bool = False
    is_violation: bool = False


class Cluster(BaseModel):
    """A logical directory/domain grouping of module nodes."""

    id: str
    label: str
    directory: PosixRelPath
    node_ids: list[PosixRelPath] = Field(default_factory=list)
    primary_layer: Layer = Layer.SHARED


class CodeGraph(BaseModel):
    """Complete module dependency graph for a scanned repository."""

    repo_name: str
    root_path: PosixPathStr
    truncated: bool = False
    total_files_seen: int = Field(default=0, ge=0)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    clusters: list[Cluster] = Field(default_factory=list)
    warnings: list[ParseWarning] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
