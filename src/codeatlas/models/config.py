"""Configuration and enum models for CodeAtlas scanning and analysis."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Language(StrEnum):
    """Supported source programming languages."""

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    PHP = "php"
    GO = "go"


class Layer(StrEnum):
    """Architectural layers assigned to scanned source modules."""

    ENTRYPOINT = "entrypoint"
    FRONTEND = "frontend"
    SERVICE = "service"
    DATA = "data"
    SHARED = "shared"
    CONFIG = "config"
    TEST = "test"


class ExportFormat(StrEnum):
    """Supported output formats for CodeAtlas exports."""

    CONSOLE = "console"
    JSON = "json"
    MARKDOWN = "markdown"
    MERMAID = "mermaid"
    LLMS = "llms"
    HTML = "html"


class ThemeMode(StrEnum):
    """UI visual themes supported by HTML exports and web explorer."""

    OBSIDIAN = "obsidian"
    PAPER = "paper"


class AnalysisThresholds(BaseModel):
    """Thresholds used when detecting God Module hotspots."""

    god_loc: int = Field(default=350, ge=1)
    god_fan_out: int = Field(default=10, ge=1)
    god_fan_in: int = Field(default=12, ge=1)
    god_complexity: int = Field(default=25, ge=1)


class ScanConfig(BaseModel):
    """Configuration controlling repository walking and architectural analysis."""

    max_files: int = Field(default=3000, ge=1)
    max_file_bytes: int = Field(default=1_048_576, ge=1)
    include_tests: bool = True
    exclude_globs: list[str] = Field(default_factory=list)
    thresholds: AnalysisThresholds = Field(default_factory=AnalysisThresholds)
