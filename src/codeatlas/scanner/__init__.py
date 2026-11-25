"""Repository walking and language parser registry utilities."""

from __future__ import annotations

from codeatlas.scanner.parser_registry import (
    ParsedSyntax,
    ParserBackend,
    ParserRegistry,
    detect_dialect,
    detect_language,
)
from codeatlas.scanner.walker import (
    DEFAULT_IGNORED_DIRS,
    DEFAULT_IGNORED_FILE_GLOBS,
    WalkedFile,
    WalkResult,
    walk_repository,
)

__all__ = [
    "DEFAULT_IGNORED_DIRS",
    "DEFAULT_IGNORED_FILE_GLOBS",
    "ParsedSyntax",
    "ParserBackend",
    "ParserRegistry",
    "WalkResult",
    "WalkedFile",
    "detect_dialect",
    "detect_language",
    "walk_repository",
]
