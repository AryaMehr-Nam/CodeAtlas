"""JavaScript, TypeScript, and TSX language extractor."""

from __future__ import annotations

import posixpath
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

_HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "options", "head", "all"}
)
_NON_ROUTER_RECEIVERS: frozenset[str] = frozenset(
    {
        "map",
        "set",
        "headers",
        "searchparams",
        "params",
        "query",
        "cookies",
        "cache",
        "store",
        "localstorage",
        "sessionstorage",
        "reflect",
        "axios",
        "fetch",
        "http",
        "client",
        "api",
        "prisma",
        "db",
        "knex",
    }
)
_TS_COMPLEXITY_NODES: frozenset[str] = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_case",
        "catch_clause",
        "ternary_expression",
    }
)
_DB_PACKAGES: frozenset[str] = frozenset(
    {
        "@prisma/client",
        "typeorm",
        "drizzle-orm",
        "mongoose",
        "pg",
        "mysql2",
        "sqlite3",
        "better-sqlite3",
        "knex",
        "sequelize",
    }
)
_DB_CODE_PATTERNS: tuple[str, ...] = (
    "PrismaClient",
    "@Entity(",
    "DataSource(",
    "getRepository(",
    "createQueryBuilder(",
    "pgTable(",
    "mysqlTable(",
    "sqliteTable(",
    "mongoose.model(",
    "mongoose.connect(",
    "pool.query(",
    "client.query(",
)
_NEXT_APP_ROUTE_RE = re.compile(r"^(?:(?P<prefix>.+)/)?app(?:/(?P<sub>.+))?/route\.[jt]sx?$")
_NEXT_PAGES_API_RE = re.compile(r"^(?:.+/)?pages(?P<route>/api(?:/.*)?)\.(?:[jt]sx?|mjs|cjs)$")
_NEXT_HTTP_VERBS: frozenset[str] = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
)

_ES_IMPORT_RE = re.compile(
    r"""import\s+(?:type\s+)?(?:(?P<clause>[^;'"]+?)\s+from\s+)?(['"])(?P<src>[^'"]+)\2""",
    re.MULTILINE,
)
_EXPORT_FROM_RE = re.compile(
    r"""export\s+(?:\*|\{[^}]+\})\s+from\s+(['"])(?P<src>[^'"]+)\1""",
    re.MULTILINE,
)
_REQUIRE_OR_DYNAMIC_RE = re.compile(r"""\b(?:require|import)\s*\(\s*(['"])(?P<src>[^'"]+)\1\s*\)""")
_CLASS_RE = re.compile(r"^\s*(export\s+(?:default\s+)?)?class\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
_IFACE_RE = re.compile(r"^\s*(export\s+)?interface\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
_TYPE_RE = re.compile(r"^\s*(export\s+)?type\s+([A-Za-z_$][\w$]*)\s*[=<]", re.MULTILINE)
_FN_RE = re.compile(
    r"^\s*(export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
    re.MULTILINE,
)
_ARROW_RE = re.compile(
    r"^\s*(export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
    re.MULTILINE,
)
_EXPRESS_ROUTE_RE = re.compile(
    r"""\b([A-Za-z_$][\w$]*)\.(get|post|put|delete|patch|options|head|all)\(\s*(['"`])(/[^'"`]*)\3\s*,""",
    re.MULTILINE,
)


def _is_pascal_case(name: str) -> bool:
    return bool(name and name[0].isupper() and any(ch.islower() for ch in name))


def _check_js_db_access(imports: list[ImportRef], source_text: str) -> bool:
    for ref in imports:
        raw = ref.raw
        if raw in _DB_PACKAGES or any(raw.startswith(f"{pkg}/") for pkg in _DB_PACKAGES):
            return True
    if any(pattern in source_text for pattern in _DB_CODE_PATTERNS):
        return True
    return has_sql_pattern(source_text)


class _JsTsBaseExtractor(BaseExtractor):
    """Shared implementation for JavaScript, TypeScript, and TSX extraction."""

    def extract(self, parsed: ParsedSyntax, rel_path: str) -> FileExtraction:
        result = self._init_extraction(parsed, rel_path)
        is_tsx_like = parsed.dialect in {"tsx"} or rel_path.endswith((".jsx", ".tsx"))

        if parsed.tree is not None and not parsed.has_errors:
            self._extract_tree_sitter(
                parsed.tree.root_node,
                parsed.source_bytes,
                rel_path,
                result,
                is_tsx_like=is_tsx_like,
            )
        else:
            if parsed.tree is not None:
                self._extract_tree_sitter(
                    parsed.tree.root_node,
                    parsed.source_bytes,
                    rel_path,
                    result,
                    is_tsx_like=is_tsx_like,
                )
            self._augment_regex(parsed.source_text, result, is_tsx_like=is_tsx_like)

        self._extract_nextjs_routes(rel_path, result)
        result.has_db_access = _check_js_db_access(result.imports, parsed.source_text)
        return result

    def _extract_tree_sitter(
        self,
        root: TsNode,
        source_bytes: bytes,
        rel_path: str,
        result: FileExtraction,
        *,
        is_tsx_like: bool,
    ) -> None:
        source_text = source_bytes.decode("utf-8", errors="replace")
        has_fastify = "fastify" in source_text.lower()
        exported_names: set[str] = set()
        branches = 0

        for node in iter_nodes(root):
            ntype = str(node.type)
            if ntype in _TS_COMPLEXITY_NODES:
                branches += 1
            elif ntype == "binary_expression":
                op_node = self._field_child(node, "operator")
                if op_node is not None and node_text(op_node, source_bytes) in {"&&", "||", "??"}:
                    branches += 1

            if ntype == "import_statement":
                self._ts_parse_import(node, source_bytes, result.imports)
            elif ntype == "export_statement":
                self._ts_collect_export_metadata(node, source_bytes, result.imports, exported_names)
            elif ntype == "call_expression":
                self._ts_handle_call(
                    node,
                    source_bytes,
                    result,
                    has_fastify=has_fastify,
                )
            elif ntype == "assignment_expression":
                self._ts_collect_cjs_exports(node, source_bytes, exported_names)

        result.complexity = 1 + branches

        for stmt in root.children:
            stype = str(stmt.type)
            if stype == "export_statement":
                decl = self._field_child(stmt, "declaration")
                if decl is not None:
                    self._ts_handle_declaration(
                        decl,
                        source_bytes,
                        result,
                        exported=True,
                        is_tsx_like=is_tsx_like,
                    )
                else:
                    # Handle `export default function() {}` or `export default class {}`
                    for child in stmt.children:
                        if str(child.type) in {
                            "function_declaration",
                            "class_declaration",
                            "interface_declaration",
                            "type_alias_declaration",
                            "enum_declaration",
                            "lexical_declaration",
                            "variable_declaration",
                        }:
                            self._ts_handle_declaration(
                                child,
                                source_bytes,
                                result,
                                exported=True,
                                is_tsx_like=is_tsx_like,
                            )
            else:
                self._ts_handle_declaration(
                    stmt,
                    source_bytes,
                    result,
                    exported=False,
                    is_tsx_like=is_tsx_like,
                )

        if exported_names:
            for idx, sym in enumerate(result.symbols):
                if (
                    sym.kind != SymbolKind.METHOD
                    and sym.name in exported_names
                    and not sym.exported
                ):
                    result.symbols[idx] = sym.model_copy(update={"exported": True})

        if "require.main === module" in source_text or "module === require.main" in source_text:
            result.is_entrypoint = True

    def _ts_parse_import(
        self,
        node: TsNode,
        source_bytes: bytes,
        imports: list[ImportRef],
    ) -> None:
        src_node = self._field_child(node, "source")
        if src_node is None:
            return
        raw = strip_string_literal(node_text(src_node, source_bytes))
        if not raw:
            return
        names: list[str] = []
        clause = self._first_child_by_type(node, "import_clause")
        if clause is not None:
            for child in clause.children:
                ctype = str(child.type)
                if ctype == "identifier":
                    names.append(node_text(child, source_bytes).strip())
                elif ctype == "namespace_import":
                    names.append("*")
                elif ctype == "named_imports":
                    for spec in child.children:
                        if str(spec.type) == "import_specifier":
                            name_node = self._field_child(spec, "name")
                            if name_node is not None:
                                names.append(node_text(name_node, source_bytes).strip())
        imports.append(
            ImportRef(
                raw=raw,
                names=names,
                is_relative=raw.startswith("."),
                relative_level=relative_dot_level(raw),
                line=int(node.start_point.row) + 1,
            )
        )

    def _ts_collect_export_metadata(
        self,
        node: TsNode,
        source_bytes: bytes,
        imports: list[ImportRef],
        exported_names: set[str],
    ) -> None:
        src_node = self._field_child(node, "source")
        if src_node is not None:
            raw = strip_string_literal(node_text(src_node, source_bytes))
            if not raw:
                return
            names: list[str] = []
            has_star = any(str(c.type) == "*" for c in node.children)
            if has_star:
                names.append("*")
            export_clause = self._first_child_by_type(node, "export_clause")
            if export_clause is not None:
                for spec in export_clause.children:
                    if str(spec.type) == "export_specifier":
                        name_node = self._field_child(spec, "name")
                        if name_node is not None:
                            names.append(node_text(name_node, source_bytes).strip())
            imports.append(
                ImportRef(
                    raw=raw,
                    names=names,
                    is_relative=raw.startswith("."),
                    relative_level=relative_dot_level(raw),
                    line=int(node.start_point.row) + 1,
                )
            )
            return

        # Re-export or named export without `from`: `export { A, B as C }` / `export default Foo`
        export_clause = self._first_child_by_type(node, "export_clause")
        if export_clause is not None:
            for spec in export_clause.children:
                if str(spec.type) == "export_specifier":
                    name_node = self._field_child(spec, "name")
                    if name_node is not None:
                        exported_names.add(node_text(name_node, source_bytes).strip())
        elif any(str(c.type) == "default" for c in node.children):
            for child in node.children:
                if str(child.type) == "identifier":
                    exported_names.add(node_text(child, source_bytes).strip())

    def _ts_collect_cjs_exports(
        self,
        node: TsNode,
        source_bytes: bytes,
        exported_names: set[str],
    ) -> None:
        left = self._field_child(node, "left")
        right = self._field_child(node, "right")
        if left is None or right is None:
            return
        left_text = node_text(left, source_bytes).strip()
        if left_text == "module.exports":
            if str(right.type) == "identifier":
                exported_names.add(node_text(right, source_bytes).strip())
            elif str(right.type) == "object":
                for prop in right.children:
                    ptype = str(prop.type)
                    if ptype == "shorthand_property_identifier":
                        exported_names.add(node_text(prop, source_bytes).strip())
                    elif ptype == "pair":
                        val = self._field_child(prop, "value")
                        if val is not None and str(val.type) == "identifier":
                            exported_names.add(node_text(val, source_bytes).strip())
        elif left_text.startswith("exports."):
            exported_names.add(left_text.split(".", 1)[1].strip())

    def _ts_handle_call(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
        *,
        has_fastify: bool,
    ) -> None:
        fn_node = self._field_child(node, "function")
        args_node = self._field_child(node, "arguments")
        if fn_node is None or args_node is None:
            return

        fn_type = str(fn_node.type)
        pos_args = [c for c in args_node.children if str(c.type) not in {"(", ")", ","}]
        line = int(node.start_point.row) + 1

        # Dynamic import('...') or CommonJS require('...')
        if fn_type == "import" or (
            fn_type == "identifier" and node_text(fn_node, source_bytes).strip() == "require"
        ):
            if pos_args and str(pos_args[0].type) in {"string", "template_string"}:
                arg_text = node_text(pos_args[0], source_bytes)
                if "${" in arg_text:
                    return
                raw = strip_string_literal(arg_text)
                if raw:
                    names = self._ts_cjs_bound_names(node, source_bytes)
                    result.imports.append(
                        ImportRef(
                            raw=raw,
                            names=names,
                            is_relative=raw.startswith("."),
                            relative_level=relative_dot_level(raw),
                            line=line,
                        )
                    )
            return

        # Express / Fastify routes & entrypoints (.listen, createRoot, ReactDOM.render)
        if fn_type == "member_expression":
            obj_node = self._field_child(fn_node, "object")
            prop_node = self._field_child(fn_node, "property")
            if obj_node is None or prop_node is None:
                return
            obj_name = node_text(obj_node, source_bytes).strip()
            method_name = node_text(prop_node, source_bytes).strip()
            method_lower = method_name.lower()

            if method_name == "listen" and obj_name.lower() in {"app", "server", "fastify"}:
                result.is_entrypoint = True
                return
            if obj_name == "ReactDOM" and method_name in {"render", "createRoot"}:
                result.is_entrypoint = True
                return

            if (
                method_lower in _HTTP_METHODS
                and obj_name.lower() not in _NON_ROUTER_RECEIVERS
                and len(pos_args) >= 2
                and str(pos_args[0].type) in {"string", "template_string"}
            ):
                route_path = strip_string_literal(node_text(pos_args[0], source_bytes))
                if not route_path.startswith("/") and route_path != "*":
                    return
                handler_arg = pos_args[-1]
                handler = (
                    node_text(handler_arg, source_bytes).strip()
                    if str(handler_arg.type) in {"identifier", "member_expression"}
                    else "<anonymous>"
                )
                framework = (
                    "fastify" if (has_fastify or obj_name.lower() == "fastify") else "express"
                )
                result.routes.append(
                    RouteInfo(
                        method="ANY" if method_lower == "all" else method_name.upper(),
                        path=route_path,
                        handler=handler,
                        line=line,
                        framework=framework,
                    )
                )
                return

            if (
                method_name == "route"
                and obj_name.lower() == "fastify"
                and pos_args
                and str(pos_args[0].type) == "object"
            ):
                self._ts_parse_fastify_route_obj(pos_args[0], source_bytes, result, line)
        elif fn_type == "identifier" and node_text(fn_node, source_bytes).strip() == "createRoot":
            result.is_entrypoint = True

    def _ts_cjs_bound_names(self, call_node: TsNode, source_bytes: bytes) -> list[str]:
        parent = call_node.parent
        if parent is None or str(parent.type) != "variable_declarator":
            return []
        name_node = self._field_child(parent, "name")
        if name_node is None or str(name_node.type) != "object_pattern":
            return []
        names: list[str] = []
        for child in name_node.children:
            ctype = str(child.type)
            if ctype == "shorthand_property_identifier_pattern":
                names.append(node_text(child, source_bytes).strip())
            elif ctype == "pair_pattern":
                key_node = self._field_child(child, "key")
                if key_node is not None:
                    names.append(node_text(key_node, source_bytes).strip())
        return names

    def _ts_parse_fastify_route_obj(
        self,
        obj_node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
        line: int,
    ) -> None:
        method_val = ""
        url_val = ""
        handler_val = ""
        for child in obj_node.children:
            if str(child.type) != "pair":
                continue
            key_node = self._field_child(child, "key")
            val_node = self._field_child(child, "value")
            if key_node is None or val_node is None:
                continue
            key = strip_string_literal(node_text(key_node, source_bytes))
            if key == "method" and str(val_node.type) == "string":
                method_val = strip_string_literal(node_text(val_node, source_bytes)).upper()
            elif key in {"url", "path"} and str(val_node.type) == "string":
                url_val = strip_string_literal(node_text(val_node, source_bytes))
            elif key == "handler":
                handler_val = (
                    node_text(val_node, source_bytes).strip()
                    if str(val_node.type) in {"identifier", "member_expression"}
                    else "<anonymous>"
                )
        if method_val and url_val:
            result.routes.append(
                RouteInfo(
                    method=method_val,
                    path=url_val,
                    handler=handler_val,
                    line=line,
                    framework="fastify",
                )
            )

    def _ts_handle_declaration(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
        *,
        exported: bool,
        is_tsx_like: bool,
    ) -> None:
        ntype = str(node.type)
        line = int(node.start_point.row) + 1
        end_line = max(line, int(node.end_point.row) + 1)

        if ntype == "class_declaration":
            name_node = self._field_child(node, "name")
            if name_node is None:
                return
            name = node_text(name_node, source_bytes).strip()
            result.symbols.append(
                Symbol(
                    name=name,
                    kind=SymbolKind.CLASS,
                    line=line,
                    end_line=end_line,
                    exported=exported,
                    signature=make_line_signature(node, source_bytes),
                )
            )
            body = self._field_child(node, "body")
            if body is not None:
                for member in body.children:
                    if str(member.type) == "method_definition":
                        mname_node = self._field_child(member, "name")
                        if mname_node is None:
                            continue
                        mname = node_text(mname_node, source_bytes).strip()
                        mline = int(member.start_point.row) + 1
                        mend = max(mline, int(member.end_point.row) + 1)
                        result.symbols.append(
                            Symbol(
                                name=mname,
                                kind=SymbolKind.METHOD,
                                line=mline,
                                end_line=mend,
                                exported=exported,
                                signature=make_line_signature(member, source_bytes),
                            )
                        )
            return

        if ntype == "interface_declaration":
            name_node = self._field_child(node, "name")
            if name_node is not None:
                result.symbols.append(
                    Symbol(
                        name=node_text(name_node, source_bytes).strip(),
                        kind=SymbolKind.INTERFACE,
                        line=line,
                        end_line=end_line,
                        exported=exported,
                        signature=make_line_signature(node, source_bytes),
                    )
                )
            return

        if ntype == "type_alias_declaration":
            name_node = self._field_child(node, "name")
            if name_node is not None:
                result.symbols.append(
                    Symbol(
                        name=node_text(name_node, source_bytes).strip(),
                        kind=SymbolKind.TYPE,
                        line=line,
                        end_line=end_line,
                        exported=exported,
                        signature=make_line_signature(node, source_bytes),
                    )
                )
            return

        if ntype == "enum_declaration":
            name_node = self._field_child(node, "name")
            if name_node is not None:
                result.symbols.append(
                    Symbol(
                        name=node_text(name_node, source_bytes).strip(),
                        kind=SymbolKind.ENUM,
                        line=line,
                        end_line=end_line,
                        exported=exported,
                        signature=make_line_signature(node, source_bytes),
                    )
                )
            return

        if ntype in {"function_declaration", "generator_function_declaration"}:
            name_node = self._field_child(node, "name")
            name = (
                node_text(name_node, source_bytes).strip() if name_node is not None else "default"
            )
            kind = self._classify_js_callable(node, name, source_bytes, is_tsx_like=is_tsx_like)
            result.symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    line=line,
                    end_line=end_line,
                    exported=exported,
                    signature=make_line_signature(node, source_bytes),
                )
            )
            return

        if ntype in {"lexical_declaration", "variable_declaration"}:
            for child in node.children:
                if str(child.type) != "variable_declarator":
                    continue
                name_node = self._field_child(child, "name")
                val_node = self._field_child(child, "value")
                if name_node is None or str(name_node.type) != "identifier":
                    continue
                name = node_text(name_node, source_bytes).strip()
                cline = int(child.start_point.row) + 1
                cend = max(cline, int(child.end_point.row) + 1)
                if val_node is not None and str(val_node.type) in {
                    "arrow_function",
                    "function_expression",
                }:
                    kind = self._classify_js_callable(
                        val_node, name, source_bytes, is_tsx_like=is_tsx_like
                    )
                    result.symbols.append(
                        Symbol(
                            name=name,
                            kind=kind,
                            line=cline,
                            end_line=cend,
                            exported=exported,
                            signature=make_line_signature(node, source_bytes),
                        )
                    )
                elif exported:
                    result.symbols.append(
                        Symbol(
                            name=name,
                            kind=SymbolKind.CONSTANT,
                            line=cline,
                            end_line=cend,
                            exported=True,
                            signature=make_line_signature(node, source_bytes),
                        )
                    )

    def _classify_js_callable(
        self,
        fn_node: TsNode,
        name: str,
        source_bytes: bytes,
        *,
        is_tsx_like: bool,
    ) -> SymbolKind:
        has_jsx = any(
            str(n.type) in {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
            for n in iter_nodes(fn_node)
        )
        if has_jsx:
            return SymbolKind.COMPONENT
        ret_node = self._field_child(fn_node, "return_type")
        if ret_node is not None:
            ret_text = node_text(ret_node, source_bytes)
            if any(marker in ret_text for marker in ("JSX.Element", "ReactElement", "ReactNode")):
                return SymbolKind.COMPONENT
        if is_tsx_like and _is_pascal_case(name):
            return SymbolKind.COMPONENT
        return SymbolKind.FUNCTION

    def _extract_nextjs_routes(self, rel_path: str, result: FileExtraction) -> None:
        app_match = _NEXT_APP_ROUTE_RE.match(rel_path)
        if app_match:
            sub = app_match.group("sub") or ""
            # Ignore Next.js route groups like (marketing) when deriving URL path.
            segments = [
                s for s in sub.split("/") if s and not (s.startswith("(") and s.endswith(")"))
            ]
            route_path = "/" + "/".join(segments) if segments else "/"
            for sym in result.symbols:
                if sym.exported and sym.name in _NEXT_HTTP_VERBS:
                    result.routes.append(
                        RouteInfo(
                            method=sym.name,
                            path=route_path,
                            handler=sym.name,
                            line=sym.line,
                            framework="nextjs",
                        )
                    )
            return

        pages_match = _NEXT_PAGES_API_RE.match(rel_path)
        if pages_match:
            raw_route = pages_match.group("route")
            if raw_route.endswith("/index"):
                raw_route = raw_route[: -len("/index")] or "/"
            raw_route = posixpath.normpath(raw_route)
            handler_sym = next((s for s in result.symbols if s.exported), None)
            result.routes.append(
                RouteInfo(
                    method="ANY",
                    path=raw_route,
                    handler=handler_sym.name if handler_sym else "default",
                    line=handler_sym.line if handler_sym else 1,
                    framework="nextjs",
                )
            )

    def _augment_regex(
        self,
        source_text: str,
        result: FileExtraction,
        *,
        is_tsx_like: bool,
    ) -> None:
        seen_imports = {(imp.raw, imp.line) for imp in result.imports}
        seen_symbols = {(sym.name, sym.line) for sym in result.symbols}
        seen_routes = {(r.method, r.path, r.line) for r in result.routes}

        for match in _ES_IMPORT_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            raw = match.group("src")
            if (raw, line) not in seen_imports:
                seen_imports.add((raw, line))
                result.imports.append(
                    ImportRef(
                        raw=raw,
                        is_relative=raw.startswith("."),
                        relative_level=relative_dot_level(raw),
                        line=line,
                    )
                )

        for match in _EXPORT_FROM_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            raw = match.group("src")
            if (raw, line) not in seen_imports:
                seen_imports.add((raw, line))
                result.imports.append(
                    ImportRef(
                        raw=raw,
                        is_relative=raw.startswith("."),
                        relative_level=relative_dot_level(raw),
                        line=line,
                    )
                )

        for match in _REQUIRE_OR_DYNAMIC_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            raw = match.group("src")
            if (raw, line) not in seen_imports:
                seen_imports.add((raw, line))
                result.imports.append(
                    ImportRef(
                        raw=raw,
                        is_relative=raw.startswith("."),
                        relative_level=relative_dot_level(raw),
                        line=line,
                    )
                )

        for pattern, kind in (
            (_CLASS_RE, SymbolKind.CLASS),
            (_IFACE_RE, SymbolKind.INTERFACE),
            (_TYPE_RE, SymbolKind.TYPE),
        ):
            for match in pattern.finditer(source_text):
                line = source_text.count("\n", 0, match.start()) + 1
                name = match.group(2)
                if (name, line) not in seen_symbols:
                    seen_symbols.add((name, line))
                    result.symbols.append(
                        Symbol(
                            name=name,
                            kind=kind,
                            line=line,
                            end_line=line,
                            exported=bool(match.group(1)),
                        )
                    )

        for pattern in (_FN_RE, _ARROW_RE):
            for match in pattern.finditer(source_text):
                line = source_text.count("\n", 0, match.start()) + 1
                name = match.group(2)
                kind = (
                    SymbolKind.COMPONENT
                    if (is_tsx_like and _is_pascal_case(name))
                    else SymbolKind.FUNCTION
                )
                if (name, line) not in seen_symbols:
                    seen_symbols.add((name, line))
                    result.symbols.append(
                        Symbol(
                            name=name,
                            kind=kind,
                            line=line,
                            end_line=line,
                            exported=bool(match.group(1)),
                        )
                    )

        for match in _EXPRESS_ROUTE_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            obj = match.group(1).lower()
            verb = match.group(2).upper()
            method = "ANY" if verb == "ALL" else verb
            path = match.group(4)
            if obj not in _NON_ROUTER_RECEIVERS and (method, path, line) not in seen_routes:
                seen_routes.add((method, path, line))
                result.routes.append(
                    RouteInfo(
                        method=method,
                        path=path,
                        line=line,
                        framework="fastify" if obj == "fastify" else "express",
                    )
                )

        if result.complexity <= 1:
            branches = len(
                re.findall(r"\b(?:if|for|while|switch|case|catch)\b|&&|\|\||\?\?", source_text)
            )
            result.complexity = 1 + branches


class JavaScriptExtractor(_JsTsBaseExtractor):
    """Extractor for JavaScript / JSX / MJS / CJS files."""

    language: ClassVar[Language] = Language.JAVASCRIPT


class TypeScriptExtractor(_JsTsBaseExtractor):
    """Extractor for TypeScript / TSX / MTS / CTS files."""

    language: ClassVar[Language] = Language.TYPESCRIPT
