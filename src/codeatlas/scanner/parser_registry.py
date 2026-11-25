"""Tree-sitter parser registry with clean stdlib-ast and regex fallbacks."""

from __future__ import annotations

import ast
import posixpath
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import tree_sitter_go
import tree_sitter_javascript
import tree_sitter_php
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language as TsLanguage
from tree_sitter import Parser, Tree

from codeatlas.models.config import Language


class ParserBackend(StrEnum):
    """Parsing backend used for a given source file."""

    TREE_SITTER = "tree-sitter"
    STDLIB_AST = "stdlib-ast"
    REGEX_FALLBACK = "regex-fallback"


_EXTENSION_MAP: dict[str, tuple[Language, str]] = {
    ".py": (Language.PYTHON, "python"),
    ".pyw": (Language.PYTHON, "python"),
    ".js": (Language.JAVASCRIPT, "javascript"),
    ".jsx": (Language.JAVASCRIPT, "javascript"),
    ".mjs": (Language.JAVASCRIPT, "javascript"),
    ".cjs": (Language.JAVASCRIPT, "javascript"),
    ".ts": (Language.TYPESCRIPT, "typescript"),
    ".mts": (Language.TYPESCRIPT, "typescript"),
    ".cts": (Language.TYPESCRIPT, "typescript"),
    ".tsx": (Language.TYPESCRIPT, "tsx"),
    ".php": (Language.PHP, "php"),
    ".phtml": (Language.PHP, "php"),
    ".go": (Language.GO, "go"),
}

_LANGUAGE_DEFAULT_DIALECT: dict[Language, str] = {
    Language.PYTHON: "python",
    Language.JAVASCRIPT: "javascript",
    Language.TYPESCRIPT: "typescript",
    Language.PHP: "php",
    Language.GO: "go",
}

_GRAMMAR_PROVIDERS: dict[str, Callable[[], object]] = {
    "python": tree_sitter_python.language,
    "javascript": tree_sitter_javascript.language,
    "typescript": tree_sitter_typescript.language_typescript,
    "tsx": tree_sitter_typescript.language_tsx,
    "php": tree_sitter_php.language_php,
    "go": tree_sitter_go.language,
}


@dataclass(frozen=True, slots=True)
class ParsedSyntax:
    """Parsed syntax tree or fallback outcome for a source file."""

    language: Language
    dialect: str
    backend: ParserBackend
    tree: Tree | None
    python_ast: ast.Module | None
    source_bytes: bytes
    source_text: str
    has_errors: bool = False
    fallback_reason: str | None = None


def detect_language(path: str | Path) -> Language | None:
    """Detect the CodeAtlas ``Language`` for a file path, or ``None`` if unsupported."""
    ext = posixpath.splitext(str(path).replace("\\", "/"))[1].lower()
    entry = _EXTENSION_MAP.get(ext)
    return entry[0] if entry is not None else None


def detect_dialect(path: str | Path) -> str | None:
    """Detect the grammar dialect (including ``tsx``) for a file path."""
    ext = posixpath.splitext(str(path).replace("\\", "/"))[1].lower()
    entry = _EXTENSION_MAP.get(ext)
    return entry[1] if entry is not None else None


class ParserRegistry:
    """Registry managing modern tree-sitter parsers and clean fallback paths."""

    def __init__(self, *, enable_tree_sitter: bool = True) -> None:
        self._enable_tree_sitter = enable_tree_sitter
        self._languages: dict[str, TsLanguage] = {}
        self._load_errors: dict[str, str] = {}

    @property
    def supported_dialects(self) -> tuple[str, ...]:
        """Return all grammar dialects configured in this registry."""
        return tuple(_GRAMMAR_PROVIDERS.keys())

    def get_ts_language(self, dialect: str) -> TsLanguage | None:
        """Return a cached ``tree_sitter.Language`` for ``dialect``, or ``None``."""
        if not self._enable_tree_sitter:
            return None
        cached = self._languages.get(dialect)
        if cached is not None:
            return cached
        if dialect in self._load_errors:
            return None

        provider = _GRAMMAR_PROVIDERS.get(dialect)
        if provider is None:
            self._load_errors[dialect] = f"Unsupported grammar dialect: {dialect}"
            return None

        try:
            ts_lang = TsLanguage(provider())
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            self._load_errors[dialect] = (
                f"Failed to initialize {dialect} tree-sitter grammar: {exc}"
            )
            return None

        self._languages[dialect] = ts_lang
        return ts_lang

    def get_parser(self, dialect: str) -> Parser | None:
        """Instantiate a fresh ``tree_sitter.Parser(Language(...))`` for ``dialect``."""
        ts_lang = self.get_ts_language(dialect)
        if ts_lang is None:
            return None
        try:
            return Parser(ts_lang)
        except (RuntimeError, TypeError, ValueError) as exc:
            self._load_errors[dialect] = f"Failed to construct Parser for {dialect}: {exc}"
            return None

    def last_error(self, dialect: str) -> str | None:
        """Return any recorded initialization error for ``dialect``."""
        return self._load_errors.get(dialect)

    def parse_bytes(
        self,
        source: bytes,
        *,
        path: str | Path | None = None,
        language: Language | None = None,
        dialect: str | None = None,
    ) -> ParsedSyntax:
        """Parse raw source bytes via tree-sitter with stdlib-ast and regex fallbacks."""
        resolved_lang, resolved_dialect = self._resolve_target(
            path=path, language=language, dialect=dialect
        )
        source_text = source.decode("utf-8", errors="replace")

        tree: Tree | None = None
        ts_failure_reason: str | None = None
        parser = self.get_parser(resolved_dialect)

        if parser is not None:
            try:
                tree = parser.parse(source)
            except (RuntimeError, TypeError, ValueError, MemoryError) as exc:
                ts_failure_reason = f"tree-sitter parse failed: {exc}"
            else:
                if not tree.root_node.has_error:
                    return ParsedSyntax(
                        language=resolved_lang,
                        dialect=resolved_dialect,
                        backend=ParserBackend.TREE_SITTER,
                        tree=tree,
                        python_ast=None,
                        source_bytes=source,
                        source_text=source_text,
                        has_errors=False,
                        fallback_reason=None,
                    )
                ts_failure_reason = "tree-sitter reported syntax errors"
        else:
            ts_failure_reason = (
                self._load_errors.get(resolved_dialect) or "tree-sitter parser disabled"
            )

        if resolved_lang == Language.PYTHON:
            try:
                py_ast = ast.parse(source)
            except (SyntaxError, ValueError) as exc:
                return ParsedSyntax(
                    language=resolved_lang,
                    dialect=resolved_dialect,
                    backend=ParserBackend.REGEX_FALLBACK,
                    tree=tree,
                    python_ast=None,
                    source_bytes=source,
                    source_text=source_text,
                    has_errors=True,
                    fallback_reason=f"{ts_failure_reason}; stdlib-ast failed: {exc}",
                )
            return ParsedSyntax(
                language=resolved_lang,
                dialect=resolved_dialect,
                backend=ParserBackend.STDLIB_AST,
                tree=tree,
                python_ast=py_ast,
                source_bytes=source,
                source_text=source_text,
                has_errors=False,
                fallback_reason=f"{ts_failure_reason}; fell back to stdlib-ast",
            )

        return ParsedSyntax(
            language=resolved_lang,
            dialect=resolved_dialect,
            backend=ParserBackend.REGEX_FALLBACK,
            tree=tree,
            python_ast=None,
            source_bytes=source,
            source_text=source_text,
            has_errors=True,
            fallback_reason=f"{ts_failure_reason}; fell back to regex-fallback",
        )

    def parse_text(
        self,
        source: str,
        *,
        path: str | Path | None = None,
        language: Language | None = None,
        dialect: str | None = None,
    ) -> ParsedSyntax:
        """Parse UTF-8 source text via ``parse_bytes``."""
        return self.parse_bytes(
            source.encode("utf-8"),
            path=path,
            language=language,
            dialect=dialect,
        )

    def parse_file(self, path: str | Path) -> ParsedSyntax:
        """Read and parse a source file from disk."""
        file_path = Path(path)
        return self.parse_bytes(file_path.read_bytes(), path=file_path)

    @staticmethod
    def _resolve_target(
        *,
        path: str | Path | None,
        language: Language | None,
        dialect: str | None,
    ) -> tuple[Language, str]:
        detected_lang = detect_language(path) if path is not None else None
        detected_dialect = detect_dialect(path) if path is not None else None

        resolved_lang = language or detected_lang
        if resolved_lang is None:
            raise ValueError(f"Unsupported or unknown source language for path={path!r}")

        resolved_dialect = dialect or detected_dialect or _LANGUAGE_DEFAULT_DIALECT[resolved_lang]
        if resolved_dialect not in _GRAMMAR_PROVIDERS:
            raise ValueError(f"Unsupported parser dialect: {resolved_dialect!r}")

        return resolved_lang, resolved_dialect
