"""Language extractor registry and safe file/text extraction entrypoints."""

from __future__ import annotations

from pathlib import Path

from codeatlas.models.config import Language
from codeatlas.models.graph import ParseWarning, normalize_posix_rel_path
from codeatlas.scanner.extractors.base import BaseExtractor, FileExtraction, count_lines
from codeatlas.scanner.extractors.go_ext import GoExtractor
from codeatlas.scanner.extractors.js_ts_ext import JavaScriptExtractor, TypeScriptExtractor
from codeatlas.scanner.extractors.php_ext import PhpExtractor
from codeatlas.scanner.extractors.python_ext import PythonExtractor
from codeatlas.scanner.parser_registry import (
    ParserBackend,
    ParserRegistry,
    detect_language,
)
from codeatlas.scanner.walker import WalkedFile

_EXTRACTORS: dict[Language, BaseExtractor] = {
    Language.PYTHON: PythonExtractor(),
    Language.JAVASCRIPT: JavaScriptExtractor(),
    Language.TYPESCRIPT: TypeScriptExtractor(),
    Language.PHP: PhpExtractor(),
    Language.GO: GoExtractor(),
}

_DEFAULT_EXT_BY_LANGUAGE: dict[Language, str] = {
    Language.PYTHON: "module.py",
    Language.JAVASCRIPT: "module.js",
    Language.TYPESCRIPT: "module.ts",
    Language.PHP: "module.php",
    Language.GO: "module.go",
}


def get_extractor(language: Language) -> BaseExtractor:
    """Return the extractor instance registered for ``language``."""
    extractor = _EXTRACTORS.get(language)
    if extractor is None:
        raise ValueError(f"No extractor registered for language: {language!r}")
    return extractor


def extract_text(
    source: str | bytes,
    path: str | Path = "",
    registry: ParserRegistry | None = None,
    *,
    language: Language | None = None,
    dialect: str | None = None,
) -> FileExtraction:
    """Extract structural metadata from raw source text or bytes without crashing."""
    active_registry = registry or ParserRegistry()
    rel_path = normalize_posix_rel_path(str(path)) if path else ""
    resolved_lang = language or (detect_language(rel_path) if rel_path else None)
    if resolved_lang is None:
        raise ValueError(f"Unsupported or unknown language for path={path!r}")

    effective_path = rel_path or _DEFAULT_EXT_BY_LANGUAGE[resolved_lang]

    source_bytes = source if isinstance(source, bytes) else source.encode("utf-8", errors="replace")

    try:
        parsed = active_registry.parse_bytes(
            source_bytes,
            path=effective_path,
            language=resolved_lang,
            dialect=dialect,
        )
        extractor = get_extractor(parsed.language)
        return extractor.extract(parsed, effective_path)
    except Exception as exc:
        source_text = source_bytes.decode("utf-8", errors="replace")
        loc, code_lines, comment_lines = count_lines(source_text, resolved_lang)
        return FileExtraction(
            path=effective_path,
            language=resolved_lang,
            loc=loc,
            code_lines=code_lines,
            comment_lines=comment_lines,
            complexity=1,
            backend=ParserBackend.REGEX_FALLBACK,
            warnings=[
                ParseWarning(
                    path=effective_path,
                    message=f"Extractor failed softly: {exc}",
                    fallback_used=ParserBackend.REGEX_FALLBACK.value,
                )
            ],
        )


def extract_file(walked_file: WalkedFile, registry: ParserRegistry) -> FileExtraction:
    """Read ``walked_file`` as UTF-8 (with replacement) and extract metadata."""
    rel_path = normalize_posix_rel_path(walked_file.relative_path)
    resolved_lang = walked_file.language or detect_language(rel_path)
    if resolved_lang is None:
        raise ValueError(f"Unsupported source language for {rel_path!r}")

    try:
        raw_text = walked_file.path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return FileExtraction(
            path=rel_path,
            language=resolved_lang,
            loc=0,
            code_lines=0,
            comment_lines=0,
            complexity=1,
            backend=ParserBackend.REGEX_FALLBACK,
            warnings=[
                ParseWarning(
                    path=rel_path,
                    message=f"Unable to read file: {exc}",
                    fallback_used=ParserBackend.REGEX_FALLBACK.value,
                )
            ],
        )

    return extract_text(
        raw_text,
        path=rel_path,
        registry=registry,
        language=resolved_lang,
    )


__all__ = [
    "BaseExtractor",
    "FileExtraction",
    "GoExtractor",
    "JavaScriptExtractor",
    "PhpExtractor",
    "PythonExtractor",
    "TypeScriptExtractor",
    "count_lines",
    "extract_file",
    "extract_text",
    "get_extractor",
]
