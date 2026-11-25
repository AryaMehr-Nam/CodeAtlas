"""Base extraction models, line counting, and shared AST/regex utilities."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

from tree_sitter import Node as TsNode

from codeatlas.models.config import Language
from codeatlas.models.graph import (
    ImportRef,
    ParseWarning,
    RouteInfo,
    Symbol,
    normalize_posix_rel_path,
)
from codeatlas.scanner.parser_registry import ParsedSyntax, ParserBackend

_SQL_QUERY_RE = re.compile(
    r"(?is)\b(?:SELECT\s+.+?\s+FROM|INSERT\s+INTO|UPDATE\s+[A-Za-z_\"`\[]\w*\s+SET|"
    r"DELETE\s+FROM|CREATE\s+TABLE)\b"
)


@dataclass(slots=True)
class FileExtraction:
    """Structured extraction result for a single parsed source file."""

    path: str
    language: Language
    loc: int
    code_lines: int
    comment_lines: int
    complexity: int = 1
    imports: list[ImportRef] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    routes: list[RouteInfo] = field(default_factory=list)
    is_entrypoint: bool = False
    has_db_access: bool = False
    package_or_namespace: str | None = None
    backend: ParserBackend = ParserBackend.TREE_SITTER
    warnings: list[ParseWarning] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.path:
            self.path = normalize_posix_rel_path(self.path)


def _count_python_lines(lines: list[str]) -> tuple[int, int, int]:
    """Count total, code, and comment lines for Python source text."""
    loc = len(lines)
    code_lines = 0
    comment_lines = 0
    triple_delim: str | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if triple_delim is not None:
            code_lines += 1
            idx = 0
            while idx < len(line):
                if line[idx] == "\\":
                    idx += 2
                    continue
                if line.startswith(triple_delim, idx):
                    idx += 3
                    triple_delim = None
                    # Scan remainder of line in case another triple string starts.
                    while idx < len(line):
                        ch = line[idx]
                        if ch == "#":
                            break
                        if line.startswith('"""', idx) or line.startswith("'''", idx):
                            triple_delim = line[idx : idx + 3]
                            idx += 3
                            break
                        if ch in {'"', "'"}:
                            quote = ch
                            idx += 1
                            while idx < len(line):
                                if line[idx] == "\\":
                                    idx += 2
                                    continue
                                if line[idx] == quote:
                                    idx += 1
                                    break
                                idx += 1
                            continue
                        idx += 1
                    break
                idx += 1
            continue

        if stripped.startswith("#"):
            comment_lines += 1
            continue

        code_lines += 1
        idx = 0
        while idx < len(line):
            ch = line[idx]
            if ch == "#":
                break
            if line.startswith('"""', idx) or line.startswith("'''", idx):
                delim = line[idx : idx + 3]
                idx += 3
                closed = False
                while idx < len(line):
                    if line[idx] == "\\":
                        idx += 2
                        continue
                    if line.startswith(delim, idx):
                        idx += 3
                        closed = True
                        break
                    idx += 1
                if not closed:
                    triple_delim = delim
                    break
                continue
            if ch in {'"', "'"}:
                quote = ch
                idx += 1
                while idx < len(line):
                    if line[idx] == "\\":
                        idx += 2
                        continue
                    if line[idx] == quote:
                        idx += 1
                        break
                    idx += 1
                continue
            idx += 1

    return loc, code_lines, comment_lines


def _count_c_style_lines(
    lines: list[str],
    *,
    allow_hash_comment: bool = False,
    allow_backtick_multiline: bool = True,
    backtick_escapes: bool = True,
) -> tuple[int, int, int]:
    """Count total, code, and comment lines for JS/TS, PHP, and Go source text."""
    loc = len(lines)
    code_lines = 0
    comment_lines = 0
    in_block_comment = False
    in_backtick = False

    for line in lines:
        if not line.strip():
            continue

        has_code = False
        has_comment = False
        idx = 0
        length = len(line)

        while idx < length:
            if in_block_comment:
                has_comment = True
                end_idx = line.find("*/", idx)
                if end_idx == -1:
                    idx = length
                    break
                in_block_comment = False
                idx = end_idx + 2
                continue

            if in_backtick:
                has_code = True
                while idx < length:
                    if backtick_escapes and line[idx] == "\\":
                        idx += 2
                        continue
                    if line[idx] == "`":
                        in_backtick = False
                        idx += 1
                        break
                    idx += 1
                continue

            ch = line[idx]
            if ch.isspace():
                idx += 1
                continue

            if line.startswith("//", idx):
                has_comment = True
                break

            # In PHP 8+, #[Attr] is an attribute while # without '[' is a line comment.
            if allow_hash_comment and ch == "#" and not line.startswith("#[", idx):
                has_comment = True
                break

            if line.startswith("/*", idx):
                has_comment = True
                in_block_comment = True
                idx += 2
                continue

            if ch in {'"', "'"} or (allow_backtick_multiline and ch == "`"):
                has_code = True
                quote = ch
                idx += 1
                if quote == "`":
                    closed = False
                    while idx < length:
                        if backtick_escapes and line[idx] == "\\":
                            idx += 2
                            continue
                        if line[idx] == "`":
                            closed = True
                            idx += 1
                            break
                        idx += 1
                    if not closed:
                        in_backtick = True
                else:
                    while idx < length:
                        if line[idx] == "\\":
                            idx += 2
                            continue
                        if line[idx] == quote:
                            idx += 1
                            break
                        idx += 1
                continue

            has_code = True
            idx += 1

        if has_code:
            code_lines += 1
        elif has_comment:
            comment_lines += 1

    return loc, code_lines, comment_lines


def count_lines(source_text: str, language: Language) -> tuple[int, int, int]:
    """Return ``(loc, code_lines, comment_lines)`` for ``source_text``."""
    if not source_text:
        return 0, 0, 0

    lines = source_text.splitlines()
    if language == Language.PYTHON:
        return _count_python_lines(lines)
    if language == Language.PHP:
        return _count_c_style_lines(
            lines,
            allow_hash_comment=True,
            allow_backtick_multiline=False,
        )
    if language == Language.GO:
        return _count_c_style_lines(
            lines,
            allow_hash_comment=False,
            allow_backtick_multiline=True,
            backtick_escapes=False,
        )
    return _count_c_style_lines(
        lines,
        allow_hash_comment=False,
        allow_backtick_multiline=True,
        backtick_escapes=True,
    )


def node_text(node: TsNode, source_bytes: bytes) -> str:
    """Extract UTF-8 text covered by a tree-sitter AST node."""
    start = int(node.start_byte)
    end = int(node.end_byte)
    return source_bytes[start:end].decode("utf-8", errors="replace")


def iter_nodes(root: TsNode) -> Iterator[TsNode]:
    """Iterate all tree-sitter AST nodes depth-first without Python recursion."""
    stack: list[TsNode] = [root]
    while stack:
        current = stack.pop()
        yield current
        children = list(current.children)
        for child in reversed(children):
            stack.append(child)


def strip_string_literal(raw: str) -> str:
    """Strip surrounding quotes (and optional Python prefixes) from a literal."""
    cleaned = raw.strip()
    if not cleaned:
        return ""
    prefix_match = re.match(r"^[rRuUbBfF]+(?=['\"])", cleaned)
    if prefix_match:
        cleaned = cleaned[prefix_match.end() :]
    for triple in ('"""', "'''"):
        if len(cleaned) >= 6 and cleaned.startswith(triple) and cleaned.endswith(triple):
            return cleaned[3:-3]
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'", "`"}:
        return cleaned[1:-1]
    return cleaned


def relative_dot_level(specifier: str) -> int:
    """Compute relative directory traversal depth for slash-based import paths."""
    if not specifier.startswith("."):
        return 0
    if specifier == "." or specifier.startswith("./"):
        return 1
    parts = specifier.split("/")
    dots = 0
    for part in parts:
        if part == "..":
            dots += 1
        elif part == ".":
            continue
        else:
            break
    return max(1, 1 + dots)


def has_sql_pattern(text: str) -> bool:
    """Return True when ``text`` contains an inline SQL statement pattern."""
    return bool(_SQL_QUERY_RE.search(text))


def build_parse_warnings(parsed: ParsedSyntax, rel_path: str) -> list[ParseWarning]:
    """Build non-fatal parse warnings when syntax errors or fallbacks occurred."""
    if not parsed.has_errors and parsed.fallback_reason is None:
        return []
    fallback_used = parsed.backend.value if parsed.backend != ParserBackend.TREE_SITTER else None
    message = parsed.fallback_reason or "Syntax errors encountered during parsing."
    return [
        ParseWarning(
            path=rel_path,
            message=message,
            fallback_used=fallback_used,
        )
    ]


def collapse_whitespace(text: str) -> str:
    """Normalize multi-line signatures into a clean single-line summary."""
    return " ".join(text.split())


def make_line_signature(node: TsNode, source_bytes: bytes, *, max_len: int = 120) -> str:
    """Extract the declaration header line of a node as a concise signature."""
    raw = node_text(node, source_bytes)
    first_line = raw.splitlines()[0].strip() if raw else ""
    compact = collapse_whitespace(first_line)
    if compact.endswith("{"):
        compact = compact[:-1].rstrip()
    if len(compact) > max_len:
        return f"{compact[: max_len - 3]}..."
    return compact


class BaseExtractor(ABC):
    """Abstract base class for language-specific source extractors."""

    language: ClassVar[Language]

    @abstractmethod
    def extract(self, parsed: ParsedSyntax, rel_path: str) -> FileExtraction:
        """Extract symbols, imports, routes, metrics, and flags from ``parsed``."""

    def _init_extraction(self, parsed: ParsedSyntax, rel_path: str) -> FileExtraction:
        """Create a pre-populated ``FileExtraction`` with line metrics and warnings."""
        loc, code_lines, comment_lines = count_lines(parsed.source_text, self.language)
        return FileExtraction(
            path=rel_path,
            language=self.language,
            loc=loc,
            code_lines=code_lines,
            comment_lines=comment_lines,
            complexity=1,
            backend=parsed.backend,
            warnings=build_parse_warnings(parsed, rel_path),
        )

    @staticmethod
    def _first_child_by_type(node: TsNode, *node_types: str) -> TsNode | None:
        type_set = set(node_types)
        for child in node.children:
            if str(child.type) in type_set:
                return child
        return None

    @staticmethod
    def _field_child(node: TsNode, field_name: str) -> TsNode | None:
        child: TsNode | None = node.child_by_field_name(field_name)
        return child
