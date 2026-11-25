"""Output exporters and file-writing utilities for CodeAtlas analysis results."""

from __future__ import annotations

from pathlib import Path

from codeatlas.exporters._common import normalize_newlines
from codeatlas.exporters.html_bundle_exporter import export_html_bundle
from codeatlas.exporters.json_exporter import export_json
from codeatlas.exporters.llm_context_exporter import estimate_tokens, export_llms_txt
from codeatlas.exporters.markdown_exporter import export_markdown
from codeatlas.exporters.mermaid_exporter import export_mermaid
from codeatlas.models.analysis import AnalysisResult
from codeatlas.models.config import ExportFormat, ThemeMode


def export_analysis(
    result: AnalysisResult,
    fmt: ExportFormat,
    *,
    max_tokens: int = 8000,
    default_theme: ThemeMode = ThemeMode.OBSIDIAN,
) -> str:
    """Render an ``AnalysisResult`` in the requested file export format."""
    resolved_fmt = ExportFormat(fmt)
    if resolved_fmt == ExportFormat.JSON:
        return export_json(result)
    if resolved_fmt == ExportFormat.MERMAID:
        return export_mermaid(result)
    if resolved_fmt == ExportFormat.MARKDOWN:
        return export_markdown(result)
    if resolved_fmt == ExportFormat.LLMS:
        return export_llms_txt(result, max_tokens=max_tokens)
    if resolved_fmt == ExportFormat.HTML:
        return export_html_bundle(result, default_theme=default_theme)
    raise ValueError(f"Unsupported file export format: {resolved_fmt.value}")


def write_export(output_path: str | Path, content: str) -> None:
    """Write exported text to disk using UTF-8 encoding and strict LF (``\\n``) newlines."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_newlines(content)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(normalized)


__all__ = [
    "estimate_tokens",
    "export_analysis",
    "export_html_bundle",
    "export_json",
    "export_llms_txt",
    "export_markdown",
    "export_mermaid",
    "write_export",
]
