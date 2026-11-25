"""PHP language extractor using tree-sitter primary + regex fallback."""

from __future__ import annotations

import re
from typing import ClassVar

from tree_sitter import Node as TsNode

from codeatlas.models.config import Language
from codeatlas.models.graph import ImportRef, RouteInfo, Symbol, SymbolKind
from codeatlas.scanner.extractors.base import (
    BaseExtractor,
    FileExtraction,
    collapse_whitespace,
    has_sql_pattern,
    iter_nodes,
    make_line_signature,
    node_text,
    relative_dot_level,
    strip_string_literal,
)
from codeatlas.scanner.parser_registry import ParsedSyntax

_LARAVEL_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "options", "any", "match"}
)
_TS_COMPLEXITY_NODES: frozenset[str] = frozenset(
    {
        "if_statement",
        "else_if_clause",
        "for_statement",
        "foreach_statement",
        "while_statement",
        "do_statement",
        "case_statement",
        "catch_clause",
        "conditional_expression",
        "match_conditional_expression",
    }
)
_PHP_DB_BASES: frozenset[str] = frozenset({"Model", "Authenticatable", "Pivot"})
_PHP_DB_PATTERNS: tuple[str, ...] = (
    "DB::",
    "Illuminate\\Support\\Facades\\DB",
    "Doctrine\\",
    "EntityManager",
    "EntityRepository",
    "ORM\\Entity",
    "createQueryBuilder",
    "new PDO(",
    "PDO::",
    "mysqli_",
    "->executeQuery(",
    "->prepare(",
)

_PHP_NS_RE = re.compile(r"^\s*namespace\s+([A-Za-z_\\][A-Za-z0-9_\\]*)\s*[;{]", re.MULTILINE)
_PHP_GROUP_USE_RE = re.compile(
    r"^\s*use\s+(?:(?:function|const)\s+)?\\?([A-Za-z_\\][A-Za-z0-9_\\]*?)\\\{([^}]+)\}\s*;",
    re.MULTILINE,
)
_PHP_SINGLE_USE_RE = re.compile(
    r"^\s*use\s+(?:(?:function|const)\s+)?\\?([A-Za-z_][A-Za-z0-9_]*(?:\\[A-Za-z_][A-Za-z0-9_]*)*)"
    r"(?:\s+as\s+[A-Za-z_]\w*)?\s*;",
    re.MULTILINE,
)
_PHP_REQUIRE_RE = re.compile(
    r"""\b(?:require_once|require|include_once|include)\s*(?:\(\s*)?(?:(__DIR__|dirname\s*\(\s*__DIR__\s*\))\s*\.\s*)?(['"])([^'"]+)\2""",
    re.MULTILINE,
)
_PHP_SYMBOL_RE = re.compile(
    r"^\s*(?:abstract\s+|final\s+|readonly\s+)?(class|interface|trait|enum)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)
_PHP_FN_RE = re.compile(
    r"^\s*(?:(public|protected|private)\s+)?(?:static\s+)?function\s+([A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)
_PHP_LARAVEL_ROUTE_RE = re.compile(
    r"""Route::(get|post|put|patch|delete|options|any)\(\s*(['"])([^'"]+)\2""",
    re.MULTILINE,
)
_PHP_SYMFONY_ROUTE_RE = re.compile(
    r"""#\[Route\(\s*(?:path:\s*)?(['"])([^'"]+)\1(?:[^\]]*methods:\s*\[([^\]]+)\])?""",
    re.MULTILINE,
)


def _join_route_prefix(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if not path or path == "/":
        return prefix
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def _check_php_db_access(source_text: str, imports: list[ImportRef]) -> bool:
    for ref in imports:
        if (
            ref.raw.startswith("Doctrine\\")
            or ref.raw == "Illuminate\\Support\\Facades\\DB"
            or ref.raw.endswith("\\Model")
        ):
            return True
    if any(pattern in source_text for pattern in _PHP_DB_PATTERNS):
        return True
    if re.search(r"\bextends\s+(?:\\?[A-Za-z_\\]+\\)?Model\b", source_text):
        return True
    return has_sql_pattern(source_text)


class PhpExtractor(BaseExtractor):
    """Extractor for PHP files using tree-sitter primary + regex fallback."""

    language: ClassVar[Language] = Language.PHP

    def extract(self, parsed: ParsedSyntax, rel_path: str) -> FileExtraction:
        result = self._init_extraction(parsed, rel_path)
        if rel_path == "index.php" or rel_path.endswith("/index.php"):
            result.is_entrypoint = True

        if parsed.tree is not None and not parsed.has_errors:
            self._extract_tree_sitter(parsed.tree.root_node, parsed.source_bytes, result)
        else:
            if parsed.tree is not None:
                self._extract_tree_sitter(parsed.tree.root_node, parsed.source_bytes, result)
            self._augment_regex(parsed.source_text, result)

        result.has_db_access = _check_php_db_access(parsed.source_text, result.imports)
        return result

    def _extract_tree_sitter(
        self,
        root: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
    ) -> None:
        branches = 0

        for node in iter_nodes(root):
            ntype = str(node.type)
            if ntype in _TS_COMPLEXITY_NODES:
                branches += 1
            elif ntype == "binary_expression":
                op_node = self._field_child(node, "operator")
                if op_node is not None and node_text(op_node, source_bytes).lower() in {
                    "&&",
                    "||",
                    "??",
                    "and",
                    "or",
                }:
                    branches += 1

            if ntype == "namespace_definition" and result.package_or_namespace is None:
                name_node = self._field_child(node, "name")
                if name_node is not None:
                    result.package_or_namespace = (
                        node_text(name_node, source_bytes).strip().lstrip("\\")
                    )
            elif ntype == "namespace_use_declaration":
                self._ts_parse_use(node, source_bytes, result.imports)
            elif ntype in {
                "require_expression",
                "require_once_expression",
                "include_expression",
                "include_once_expression",
            }:
                self._ts_parse_require(node, source_bytes, result.imports)
            elif ntype in {
                "class_declaration",
                "interface_declaration",
                "trait_declaration",
                "enum_declaration",
            }:
                self._ts_handle_type_decl(node, source_bytes, result)
            elif ntype == "function_definition":
                self._ts_handle_function(node, source_bytes, result)
            elif ntype == "scoped_call_expression":
                self._ts_handle_laravel_route(node, source_bytes, result)

        result.complexity = 1 + branches

    def _ts_parse_use(
        self,
        node: TsNode,
        source_bytes: bytes,
        imports: list[ImportRef],
    ) -> None:
        line = int(node.start_point.row) + 1
        prefix_node = self._first_child_by_type(node, "namespace_name")
        group_node = self._field_child(node, "body") or self._first_child_by_type(
            node, "namespace_use_group"
        )

        if group_node is not None and prefix_node is not None:
            prefix = node_text(prefix_node, source_bytes).strip().strip("\\")
            for clause in group_node.children:
                if str(clause.type) != "namespace_use_clause":
                    continue
                target = self._extract_use_clause_target(clause, source_bytes)
                if not target:
                    continue
                fqn = f"{prefix}\\{target}" if prefix else target
                leaf = fqn.rsplit("\\", 1)[-1]
                imports.append(ImportRef(raw=fqn, names=[leaf], line=line))
            return

        for child in node.children:
            if str(child.type) == "namespace_use_clause":
                fqn = self._extract_use_clause_target(child, source_bytes)
                if fqn:
                    leaf = fqn.rsplit("\\", 1)[-1]
                    imports.append(ImportRef(raw=fqn, names=[leaf], line=line))

    def _extract_use_clause_target(self, clause: TsNode, source_bytes: bytes) -> str:
        for child in clause.children:
            ctype = str(child.type)
            if ctype == "as":
                break
            if ctype in {"qualified_name", "name", "namespace_name"}:
                return node_text(child, source_bytes).strip().lstrip("\\")
        return ""

    def _ts_parse_require(
        self,
        node: TsNode,
        source_bytes: bytes,
        imports: list[ImportRef],
    ) -> None:
        line = int(node.start_point.row) + 1
        expr: TsNode | None = None
        for child in node.children:
            if str(child.type) not in {
                "require",
                "require_once",
                "include",
                "include_once",
                "(",
                ")",
            }:
                expr = child
                break
        if expr is None:
            return
        if str(expr.type) == "parenthesized_expression":
            for inner in expr.children:
                if str(inner.type) not in {"(", ")"}:
                    expr = inner
                    break

        raw: str | None = None
        if str(expr.type) == "string":
            path_part = strip_string_literal(node_text(expr, source_bytes))
            if path_part:
                raw = path_part if path_part.startswith(".") else f"./{path_part.lstrip('/')}"
        elif str(expr.type) == "binary_expression":
            left = self._field_child(expr, "left")
            right = self._field_child(expr, "right")
            if left is not None and right is not None and str(right.type) == "string":
                left_text = collapse_whitespace(node_text(left, source_bytes))
                rhs = strip_string_literal(node_text(right, source_bytes))
                if "__DIR__" in left_text:
                    prefix = "../" if "dirname" in left_text else "./"
                    raw = f"{prefix}{rhs.lstrip('/')}"

        if raw:
            imports.append(
                ImportRef(
                    raw=raw,
                    is_relative=True,
                    relative_level=relative_dot_level(raw),
                    line=line,
                )
            )

    def _ts_handle_type_decl(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
    ) -> None:
        ntype = str(node.type)
        kind_map = {
            "class_declaration": SymbolKind.CLASS,
            "interface_declaration": SymbolKind.INTERFACE,
            "trait_declaration": SymbolKind.TRAIT,
            "enum_declaration": SymbolKind.ENUM,
        }
        kind = kind_map[ntype]
        name_node = self._field_child(node, "name")
        if name_node is None:
            return
        name = node_text(name_node, source_bytes).strip()
        line = int(node.start_point.row) + 1
        end_line = max(line, int(node.end_point.row) + 1)

        base_clause = self._first_child_by_type(node, "base_clause")
        if base_clause is not None:
            base_text = node_text(base_clause, source_bytes)
            if any(b in base_text for b in _PHP_DB_BASES):
                result.has_db_access = True

        result.symbols.append(
            Symbol(
                name=name,
                kind=kind,
                line=line,
                end_line=end_line,
                exported=True,
                signature=make_line_signature(node, source_bytes),
            )
        )

        class_routes = self._ts_extract_symfony_routes(node, source_bytes, handler=name, prefix="")
        class_prefix = class_routes[0].path if class_routes else ""

        body = self._field_child(node, "body")
        method_routes_found = False
        if body is not None:
            for member in body.children:
                if str(member.type) != "method_declaration":
                    continue
                m_name_node = self._field_child(member, "name")
                if m_name_node is None:
                    continue
                m_name = node_text(m_name_node, source_bytes).strip()
                m_line = int(member.start_point.row) + 1
                m_end = max(m_line, int(member.end_point.row) + 1)
                vis = [
                    node_text(c, source_bytes).strip().lower()
                    for c in member.children
                    if str(c.type) == "visibility_modifier"
                ]
                exported = not any(v in {"private", "protected"} for v in vis)
                result.symbols.append(
                    Symbol(
                        name=m_name,
                        kind=SymbolKind.METHOD,
                        line=m_line,
                        end_line=m_end,
                        exported=exported,
                        signature=make_line_signature(member, source_bytes),
                    )
                )
                m_routes = self._ts_extract_symfony_routes(
                    member,
                    source_bytes,
                    handler=f"{name}::{m_name}",
                    prefix=class_prefix,
                )
                if m_routes:
                    method_routes_found = True
                    result.routes.extend(m_routes)

        if class_routes and not method_routes_found:
            result.routes.extend(class_routes)

    def _ts_handle_function(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
    ) -> None:
        name_node = self._field_child(node, "name")
        if name_node is None:
            return
        name = node_text(name_node, source_bytes).strip()
        line = int(node.start_point.row) + 1
        end_line = max(line, int(node.end_point.row) + 1)
        result.symbols.append(
            Symbol(
                name=name,
                kind=SymbolKind.FUNCTION,
                line=line,
                end_line=end_line,
                exported=True,
                signature=make_line_signature(node, source_bytes),
            )
        )

    def _ts_extract_symfony_routes(
        self,
        node: TsNode,
        source_bytes: bytes,
        *,
        handler: str,
        prefix: str,
    ) -> list[RouteInfo]:
        attr_list = self._field_child(node, "attributes") or self._first_child_by_type(
            node, "attribute_list"
        )
        if attr_list is None:
            return []

        routes: list[RouteInfo] = []
        for group in attr_list.children:
            if str(group.type) != "attribute_group":
                continue
            for attr in group.children:
                if str(attr.type) != "attribute":
                    continue
                attr_text = node_text(attr, source_bytes)
                name_match = re.match(r"^(?:[A-Za-z_\\]+\\)?Route\b", attr_text.strip())
                if not name_match:
                    continue
                args_node = self._field_child(attr, "parameters") or self._first_child_by_type(
                    attr, "arguments"
                )
                if args_node is None:
                    continue
                args_text = node_text(args_node, source_bytes)
                path = self._parse_symfony_path(args_node, source_bytes)
                if path is None:
                    continue
                full_path = _join_route_prefix(prefix, path)
                methods_match = re.search(r"methods\s*:\s*\[([^\]]+)\]", args_text)
                if methods_match:
                    verbs = [
                        v.upper()
                        for _, v in re.findall(r"""(['"])([A-Za-z]+)\1""", methods_match.group(1))
                    ]
                else:
                    single_m = re.search(r"""methods\s*:\s*(['"])([A-Za-z]+)\1""", args_text)
                    verbs = [single_m.group(2).upper()] if single_m else ["ANY"]

                line = int(attr.start_point.row) + 1
                for verb in verbs:
                    routes.append(
                        RouteInfo(
                            method=verb,
                            path=full_path,
                            handler=handler,
                            line=line,
                            framework="symfony",
                        )
                    )
        return routes

    def _parse_symfony_path(self, args_node: TsNode, source_bytes: bytes) -> str | None:
        for child in args_node.children:
            if str(child.type) != "argument":
                continue
            name_node = self._field_child(child, "name")
            if name_node is not None:
                if node_text(name_node, source_bytes).strip() == "path":
                    str_node = self._first_child_by_type(child, "string")
                    if str_node is not None:
                        return strip_string_literal(node_text(str_node, source_bytes))
                continue
            str_node = self._first_child_by_type(child, "string")
            if str_node is not None:
                return strip_string_literal(node_text(str_node, source_bytes))
            break
        return None

    def _ts_handle_laravel_route(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
    ) -> None:
        scope_node = self._field_child(node, "scope")
        name_node = self._field_child(node, "name")
        args_node = self._field_child(node, "arguments")
        if scope_node is None or name_node is None or args_node is None:
            return
        scope_text = node_text(scope_node, source_bytes).strip().lstrip("\\")
        if scope_text not in {"Route", "Illuminate\\Support\\Facades\\Route"}:
            return
        method_name = node_text(name_node, source_bytes).strip().lower()
        if method_name not in _LARAVEL_METHODS:
            return

        arg_nodes = [c for c in args_node.children if str(c.type) == "argument"]
        if not arg_nodes:
            return

        line = int(node.start_point.row) + 1
        if method_name == "match":
            if len(arg_nodes) < 2:
                return
            verbs = [
                v.upper()
                for _, v in re.findall(
                    r"""(['"])([A-Za-z]+)\1""", node_text(arg_nodes[0], source_bytes)
                )
            ]
            path = strip_string_literal(node_text(arg_nodes[1], source_bytes))
            handler_node = arg_nodes[2] if len(arg_nodes) >= 3 else None
        else:
            verbs = ["ANY" if method_name == "any" else method_name.upper()]
            path = strip_string_literal(node_text(arg_nodes[0], source_bytes))
            handler_node = arg_nodes[1] if len(arg_nodes) >= 2 else None

        if not path:
            return
        handler = self._format_laravel_handler(handler_node, source_bytes)
        for verb in verbs or ["ANY"]:
            result.routes.append(
                RouteInfo(
                    method=verb,
                    path=path,
                    handler=handler,
                    line=line,
                    framework="laravel",
                )
            )

    @staticmethod
    def _format_laravel_handler(arg_node: TsNode | None, source_bytes: bytes) -> str:
        if arg_node is None:
            return ""
        text = collapse_whitespace(node_text(arg_node, source_bytes))
        if text.startswith(("fn(", "fn (", "function", "static function")):
            return "closure"
        if text.startswith("[") and "::class" in text:
            m = re.match(r"\[\s*([A-Za-z_\\]\w*)::class\s*,\s*['\"]([^'\"]+)['\"]\s*\]", text)
            if m:
                return f"{m.group(1)}::{m.group(2)}"
        return strip_string_literal(text) if text.startswith(("'", '"')) else text

    def _augment_regex(self, source_text: str, result: FileExtraction) -> None:
        if result.package_or_namespace is None:
            ns_match = _PHP_NS_RE.search(source_text)
            if ns_match:
                result.package_or_namespace = ns_match.group(1).lstrip("\\")

        seen_imports = {(imp.raw, imp.line) for imp in result.imports}
        seen_symbols = {(sym.name, sym.line) for sym in result.symbols}
        seen_routes = {(r.method, r.path, r.line) for r in result.routes}

        for match in _PHP_GROUP_USE_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            prefix = match.group(1).strip("\\")
            for item in match.group(2).split(","):
                target = item.strip().split(" as ")[0].strip().lstrip("\\")
                if target:
                    fqn = f"{prefix}\\{target}"
                    if (fqn, line) not in seen_imports:
                        seen_imports.add((fqn, line))
                        result.imports.append(
                            ImportRef(raw=fqn, names=[fqn.rsplit("\\", 1)[-1]], line=line)
                        )

        for match in _PHP_SINGLE_USE_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            fqn = match.group(1).lstrip("\\")
            if (fqn, line) not in seen_imports:
                seen_imports.add((fqn, line))
                result.imports.append(
                    ImportRef(raw=fqn, names=[fqn.rsplit("\\", 1)[-1]], line=line)
                )

        for match in _PHP_REQUIRE_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            anchor = match.group(1) or ""
            path_part = match.group(3)
            prefix = "../" if "dirname" in anchor else "./"
            raw = path_part if path_part.startswith(".") else f"{prefix}{path_part.lstrip('/')}"
            if (raw, line) not in seen_imports:
                seen_imports.add((raw, line))
                result.imports.append(
                    ImportRef(
                        raw=raw,
                        is_relative=True,
                        relative_level=relative_dot_level(raw),
                        line=line,
                    )
                )

        kind_map = {
            "class": SymbolKind.CLASS,
            "interface": SymbolKind.INTERFACE,
            "trait": SymbolKind.TRAIT,
            "enum": SymbolKind.ENUM,
        }
        for match in _PHP_SYMBOL_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            kind = kind_map[match.group(1)]
            name = match.group(2)
            if (name, line) not in seen_symbols:
                seen_symbols.add((name, line))
                result.symbols.append(
                    Symbol(name=name, kind=kind, line=line, end_line=line, exported=True)
                )

        for match in _PHP_FN_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            vis = match.group(1)
            name = match.group(2)
            kind = SymbolKind.METHOD if vis else SymbolKind.FUNCTION
            exported = vis not in {"private", "protected"}
            if (name, line) not in seen_symbols:
                seen_symbols.add((name, line))
                result.symbols.append(
                    Symbol(name=name, kind=kind, line=line, end_line=line, exported=exported)
                )

        for match in _PHP_LARAVEL_ROUTE_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            verb = match.group(1).upper()
            method = "ANY" if verb == "ANY" else verb
            path = match.group(3)
            if (method, path, line) not in seen_routes:
                seen_routes.add((method, path, line))
                result.routes.append(
                    RouteInfo(method=method, path=path, line=line, framework="laravel")
                )

        for match in _PHP_SYMFONY_ROUTE_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            path = match.group(2)
            methods_raw = match.group(3) or ""
            verbs = [v.upper() for _, v in re.findall(r"""(['"])([A-Za-z]+)\1""", methods_raw)] or [
                "ANY"
            ]
            for verb in verbs:
                if (verb, path, line) not in seen_routes:
                    seen_routes.add((verb, path, line))
                    result.routes.append(
                        RouteInfo(method=verb, path=path, line=line, framework="symfony")
                    )

        if result.complexity <= 1:
            branches = len(
                re.findall(
                    r"\b(?:if|elseif|for|foreach|while|case|catch|match)\b|&&|\|\||\?\?",
                    source_text,
                )
            )
            result.complexity = 1 + branches
