"""Go language extractor using tree-sitter primary + regex fallback."""

from __future__ import annotations

import re
from typing import ClassVar

from tree_sitter import Node as TsNode

from codeatlas.models.config import Language
from codeatlas.models.graph import ImportRef, RouteInfo, Symbol, SymbolKind
from codeatlas.scanner.extractors.base import (
    BaseExtractor,
    FileExtraction,
    has_sql_pattern,
    iter_nodes,
    make_line_signature,
    node_text,
    relative_dot_level,
    strip_string_literal,
)
from codeatlas.scanner.parser_registry import ParsedSyntax

_TS_COMPLEXITY_NODES: frozenset[str] = frozenset(
    {
        "if_statement",
        "for_statement",
        "expression_case",
        "type_case",
        "communication_case",
    }
)
_STDLIB_HANDLE_METHODS: frozenset[str] = frozenset({"HandleFunc", "Handle"})
_GIN_ECHO_METHODS: frozenset[str] = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "Any"}
)
_FIBER_METHODS: frozenset[str] = frozenset(
    {"Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All"}
)
_HTTP_VERBS: frozenset[str] = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}
)
_GO_DB_IMPORTS: tuple[str, ...] = (
    "database/sql",
    "sqlx",
    "gorm.io/gorm",
    "github.com/jinzhu/gorm",
    "github.com/jackc/pgx",
    "entgo.io/ent",
    "github.com/uptrace/bun",
)
_GO_DB_PATTERNS: tuple[str, ...] = (
    "sql.Open(",
    "sqlx.Connect(",
    "sqlx.Open(",
    "gorm.Open(",
    ".Query(",
    ".QueryRow(",
    ".Exec(",
)

_GO_PKG_RE = re.compile(r"^\s*package\s+([A-Za-z_]\w*)", re.MULTILINE)
_GO_SINGLE_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?:([A-Za-z_.]+)\s+)?(["`])([^"`]+)\2""",
    re.MULTILINE,
)
_GO_BLOCK_IMPORT_RE = re.compile(r"^\s*import\s*\((.*?)\)", re.DOTALL | re.MULTILINE)
_GO_IMPORT_LINE_RE = re.compile(r"""^\s*(?:([A-Za-z_.]+)\s+)?(["`])([^"`]+)\2""", re.MULTILINE)
_GO_TYPE_RE = re.compile(
    r"^\s*type\s+([A-Za-z_]\w*)\s+(struct|interface)\b",
    re.MULTILINE,
)
_GO_FUNC_RE = re.compile(
    r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)
_GO_ROUTE_RE = re.compile(
    r"""\b([A-Za-z_]\w*)\.(HandleFunc|Handle|GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|Any|Get|Post|Put|Delete|Patch|All)\(\s*(["`])([^"`]+)\3\s*,""",
    re.MULTILINE,
)


def _is_go_exported(name: str) -> bool:
    return bool(name and name[0].isupper())


def _split_go_http_pattern(pattern: str) -> tuple[str, str]:
    parts = pattern.strip().split(None, 1)
    if len(parts) == 2 and parts[0].upper() in _HTTP_VERBS and parts[1].startswith("/"):
        return parts[0].upper(), parts[1]
    return "ANY", pattern.strip()


def _infer_go_web_framework(imports: list[ImportRef], method_name: str) -> str:
    for ref in imports:
        if "gin-gonic/gin" in ref.raw:
            return "gin"
        if "labstack/echo" in ref.raw:
            return "echo"
        if "gofiber/fiber" in ref.raw:
            return "fiber"
    if method_name in _FIBER_METHODS:
        return "fiber"
    return "gin"


def _check_go_db_access(imports: list[ImportRef], source_text: str) -> bool:
    for ref in imports:
        if any(marker in ref.raw for marker in _GO_DB_IMPORTS):
            return True
    if any(pattern in source_text for pattern in _GO_DB_PATTERNS):
        return True
    return has_sql_pattern(source_text)


class GoExtractor(BaseExtractor):
    """Extractor for Go files using tree-sitter primary + regex fallback."""

    language: ClassVar[Language] = Language.GO

    def extract(self, parsed: ParsedSyntax, rel_path: str) -> FileExtraction:
        result = self._init_extraction(parsed, rel_path)

        if parsed.tree is not None and not parsed.has_errors:
            self._extract_tree_sitter(parsed.tree.root_node, parsed.source_bytes, result)
        else:
            if parsed.tree is not None:
                self._extract_tree_sitter(parsed.tree.root_node, parsed.source_bytes, result)
            self._augment_regex(parsed.source_text, result)

        result.has_db_access = _check_go_db_access(result.imports, parsed.source_text)
        return result

    def _extract_tree_sitter(
        self,
        root: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
    ) -> None:
        branches = 0
        route_calls: list[tuple[TsNode, str, str, list[TsNode]]] = []

        for node in iter_nodes(root):
            ntype = str(node.type)
            if ntype in _TS_COMPLEXITY_NODES:
                branches += 1
            elif ntype == "binary_expression":
                op_node = self._field_child(node, "operator")
                if op_node is not None and node_text(op_node, source_bytes) in {"&&", "||"}:
                    branches += 1

            if ntype == "package_clause" and result.package_or_namespace is None:
                pkg_node = self._first_child_by_type(node, "package_identifier")
                if pkg_node is not None:
                    result.package_or_namespace = node_text(pkg_node, source_bytes).strip()
            elif ntype == "import_spec":
                self._ts_parse_import_spec(node, source_bytes, result.imports)
            elif ntype == "type_spec":
                self._ts_handle_type_spec(node, source_bytes, result)
            elif ntype == "function_declaration":
                self._ts_handle_func(node, source_bytes, result, kind=SymbolKind.FUNCTION)
            elif ntype == "method_declaration":
                self._ts_handle_func(node, source_bytes, result, kind=SymbolKind.METHOD)
            elif ntype == "call_expression":
                fn_node = self._field_child(node, "function")
                args_node = self._field_child(node, "arguments")
                if (
                    fn_node is not None
                    and args_node is not None
                    and str(fn_node.type) == "selector_expression"
                ):
                    operand = self._field_child(fn_node, "operand")
                    field_node = self._field_child(fn_node, "field")
                    if operand is not None and field_node is not None:
                        pos_args = [
                            c for c in args_node.children if str(c.type) not in {"(", ")", ","}
                        ]
                        route_calls.append(
                            (
                                node,
                                node_text(operand, source_bytes).strip(),
                                node_text(field_node, source_bytes).strip(),
                                pos_args,
                            )
                        )

        result.complexity = 1 + branches

        for call_node, receiver, method_name, pos_args in route_calls:
            self._ts_handle_route_call(
                call_node,
                receiver,
                method_name,
                pos_args,
                source_bytes,
                result,
            )

    def _ts_parse_import_spec(
        self,
        node: TsNode,
        source_bytes: bytes,
        imports: list[ImportRef],
    ) -> None:
        path_node = self._field_child(node, "path")
        if path_node is None:
            return
        raw = strip_string_literal(node_text(path_node, source_bytes))
        if not raw:
            return
        name_node = self._field_child(node, "name")
        names: list[str] = []
        if name_node is not None:
            alias = node_text(name_node, source_bytes).strip()
            if alias and alias not in {".", "_"}:
                names.append(alias)
        imports.append(
            ImportRef(
                raw=raw,
                names=names,
                is_relative=raw.startswith("."),
                relative_level=relative_dot_level(raw),
                line=int(node.start_point.row) + 1,
            )
        )

    def _ts_handle_type_spec(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
    ) -> None:
        name_node = self._field_child(node, "name")
        type_node = self._field_child(node, "type")
        if name_node is None:
            return
        name = node_text(name_node, source_bytes).strip()
        type_kind_str = str(type_node.type) if type_node is not None else ""
        if type_kind_str == "struct_type":
            kind = SymbolKind.STRUCT
        elif type_kind_str == "interface_type":
            kind = SymbolKind.INTERFACE
        else:
            kind = SymbolKind.TYPE
        line = int(node.start_point.row) + 1
        end_line = max(line, int(node.end_point.row) + 1)
        result.symbols.append(
            Symbol(
                name=name,
                kind=kind,
                line=line,
                end_line=end_line,
                exported=_is_go_exported(name),
                signature=make_line_signature(node, source_bytes),
            )
        )

    def _ts_handle_func(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
        *,
        kind: SymbolKind,
    ) -> None:
        name_node = self._field_child(node, "name")
        if name_node is None:
            return
        name = node_text(name_node, source_bytes).strip()
        line = int(node.start_point.row) + 1
        end_line = max(line, int(node.end_point.row) + 1)
        if kind == SymbolKind.FUNCTION and name == "main":
            result.is_entrypoint = True
        result.symbols.append(
            Symbol(
                name=name,
                kind=kind,
                line=line,
                end_line=end_line,
                exported=_is_go_exported(name),
                signature=make_line_signature(node, source_bytes),
            )
        )

    def _ts_handle_route_call(
        self,
        call_node: TsNode,
        receiver: str,
        method_name: str,
        pos_args: list[TsNode],
        source_bytes: bytes,
        result: FileExtraction,
    ) -> None:
        line = int(call_node.start_point.row) + 1
        str_types = {"interpreted_string_literal", "raw_string_literal"}

        if method_name in _STDLIB_HANDLE_METHODS and len(pos_args) >= 2:
            if str(pos_args[0].type) not in str_types:
                return
            pattern = strip_string_literal(node_text(pos_args[0], source_bytes))
            verb, route_path = _split_go_http_pattern(pattern)
            handler = node_text(pos_args[1], source_bytes).strip()
            result.routes.append(
                RouteInfo(
                    method=verb,
                    path=route_path,
                    handler=handler,
                    line=line,
                    framework="net/http",
                )
            )
            return

        if (
            method_name in (_GIN_ECHO_METHODS | _FIBER_METHODS)
            and len(pos_args) >= 2
            and str(pos_args[0].type) in str_types
        ):
            route_path = strip_string_literal(node_text(pos_args[0], source_bytes))
            if not route_path.startswith("/"):
                return
            verb = "ANY" if method_name in {"Any", "All"} else method_name.upper()
            handler = node_text(pos_args[-1], source_bytes).strip()
            framework = _infer_go_web_framework(result.imports, method_name)
            result.routes.append(
                RouteInfo(
                    method=verb,
                    path=route_path,
                    handler=handler,
                    line=line,
                    framework=framework,
                )
            )
            return

        if (
            method_name == "Add"
            and len(pos_args) >= 3
            and str(pos_args[0].type) in str_types
            and str(pos_args[1].type) in str_types
            and receiver.lower() not in {"url", "header", "values", "strings"}
        ):
            verb = strip_string_literal(node_text(pos_args[0], source_bytes)).upper()
            route_path = strip_string_literal(node_text(pos_args[1], source_bytes))
            if verb in _HTTP_VERBS and route_path.startswith("/"):
                result.routes.append(
                    RouteInfo(
                        method=verb,
                        path=route_path,
                        handler=node_text(pos_args[2], source_bytes).strip(),
                        line=line,
                        framework="echo",
                    )
                )

    def _augment_regex(self, source_text: str, result: FileExtraction) -> None:
        if result.package_or_namespace is None:
            pkg_match = _GO_PKG_RE.search(source_text)
            if pkg_match:
                result.package_or_namespace = pkg_match.group(1)

        seen_imports = {(imp.raw, imp.line) for imp in result.imports}
        seen_symbols = {(sym.name, sym.line) for sym in result.symbols}
        seen_routes = {(r.method, r.path, r.line) for r in result.routes}

        for match in _GO_SINGLE_IMPORT_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            alias = match.group(1)
            raw = match.group(3)
            if (raw, line) not in seen_imports:
                seen_imports.add((raw, line))
                result.imports.append(
                    ImportRef(
                        raw=raw,
                        names=[alias] if alias and alias not in {".", "_"} else [],
                        is_relative=raw.startswith("."),
                        relative_level=relative_dot_level(raw),
                        line=line,
                    )
                )

        for block in _GO_BLOCK_IMPORT_RE.finditer(source_text):
            block_start_line = source_text.count("\n", 0, block.start(1)) + 1
            for item in _GO_IMPORT_LINE_RE.finditer(block.group(1)):
                line = block_start_line + block.group(1).count("\n", 0, item.start())
                alias = item.group(1)
                raw = item.group(3)
                if (raw, line) not in seen_imports:
                    seen_imports.add((raw, line))
                    result.imports.append(
                        ImportRef(
                            raw=raw,
                            names=[alias] if alias and alias not in {".", "_"} else [],
                            is_relative=raw.startswith("."),
                            relative_level=relative_dot_level(raw),
                            line=line,
                        )
                    )

        for match in _GO_TYPE_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            name = match.group(1)
            kind = SymbolKind.STRUCT if match.group(2) == "struct" else SymbolKind.INTERFACE
            if (name, line) not in seen_symbols:
                seen_symbols.add((name, line))
                result.symbols.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        line=line,
                        end_line=line,
                        exported=_is_go_exported(name),
                    )
                )

        for match in _GO_FUNC_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            name = match.group(1)
            is_method = match.group(0).lstrip().startswith("func (")
            kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
            if not is_method and name == "main":
                result.is_entrypoint = True
            if (name, line) not in seen_symbols:
                seen_symbols.add((name, line))
                result.symbols.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        line=line,
                        end_line=line,
                        exported=_is_go_exported(name),
                    )
                )

        for match in _GO_ROUTE_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            method_name = match.group(2)
            raw_path = match.group(4)
            if method_name in _STDLIB_HANDLE_METHODS:
                verb, path = _split_go_http_pattern(raw_path)
                framework = "net/http"
            else:
                verb = "ANY" if method_name in {"Any", "All"} else method_name.upper()
                path = raw_path
                framework = _infer_go_web_framework(result.imports, method_name)
            if (verb, path, line) not in seen_routes:
                seen_routes.add((verb, path, line))
                result.routes.append(
                    RouteInfo(method=verb, path=path, line=line, framework=framework)
                )

        if result.complexity <= 1:
            branches = len(re.findall(r"\b(?:if|for|case|select)\b|&&|\|\|", source_text))
            result.complexity = 1 + branches
