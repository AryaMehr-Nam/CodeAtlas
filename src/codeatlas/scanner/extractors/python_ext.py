"""Python language extractor using tree-sitter primary + stdlib-ast/regex fallbacks."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from typing import Any, ClassVar

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
    strip_string_literal,
)
from codeatlas.scanner.parser_registry import ParsedSyntax, ParserBackend

_ROUTE_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "options", "head", "route", "api_route"}
)
_TS_COMPLEXITY_NODES: frozenset[str] = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "case_clause",
        "conditional_expression",
        "boolean_operator",
        "if_clause",
    }
)
_DB_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "sqlalchemy",
        "sqlmodel",
        "tortoise",
        "peewee",
        "sqlite3",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "pymysql",
        "aiomysql",
        "pymongo",
        "motor",
        "databases",
    }
)
_DB_SYMBOL_PATTERNS: tuple[str, ...] = (
    "DeclarativeBase",
    "sessionmaker",
    "session_scope",
    "create_engine",
    "models.Model",
    ".objects.",
    "session.execute",
    "session.query",
    "session.scalar",
    "session.scalars",
    "session.add",
    "cursor.execute",
)

_PY_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$", re.MULTILINE)
_PY_FROM_RE = re.compile(r"^\s*from\s+(\.*[\w.]*)\s+import\s+(.+)$", re.MULTILINE)
_PY_CLASS_RE = re.compile(r"^([ \t]*)class\s+([A-Za-z_]\w*)\b(.*)$", re.MULTILINE)
_PY_DEF_RE = re.compile(r"^([ \t]*)(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)?", re.MULTILINE)
_PY_ROUTE_DECORATOR_RE = re.compile(
    r"^\s*@([A-Za-z_]\w*)\.(get|post|put|delete|patch|options|head|route|api_route)"
    r"\(\s*(['\"][^'\"]*['\"])",
    re.MULTILINE,
)
_PY_MAIN_RE = re.compile(
    r"""if\s+(?:__name__\s*==\s*['"]__main__['"]|['"]__main__['"]\s*==\s*__name__)\s*:"""
)


def _join_route_path(prefix: str, path: str) -> str:
    clean_prefix = prefix.strip()
    clean_path = path.strip()
    if not clean_prefix:
        return clean_path
    if not clean_path or clean_path == "/":
        return clean_prefix if clean_prefix.startswith("/") else f"/{clean_prefix}"
    joined = f"{clean_prefix.rstrip('/')}/{clean_path.lstrip('/')}"
    return joined if joined.startswith("/") else f"/{joined}"


def _extract_string_list_from_text(raw: str) -> list[str]:
    matches = re.findall(r"""(['"])([A-Za-z]+)\1""", raw)
    return [item[1].upper() for item in matches]


_IGNORED_PY_TOKEN_TYPES: frozenset[int] = frozenset(
    tok_type
    for name in (
        "STRING",
        "COMMENT",
        "FSTRING_START",
        "FSTRING_MIDDLE",
        "FSTRING_END",
        "TSTRING_START",
        "TSTRING_MIDDLE",
        "TSTRING_END",
    )
    if isinstance((tok_type := getattr(tokenize, name, None)), int)
)
_SKIP_LAYOUT_TOKEN_TYPES: frozenset[int] = frozenset(
    {
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NL,
        tokenize.NEWLINE,
    }
)
_PY_COMMENT_OR_STRING_RE = re.compile(
    r"#[^\n]*"
    r"|(?:(?<![A-Za-z0-9_])[rRuUbBfF]{0,2})?(?:"
    r'"""(?:[^\\"]+|\\[\s\S]|"(?!""))*(?:"""|\Z)'
    r"|'''(?:[^\\']+|\\[\s\S]|'(?!''))*(?:'''|\Z)"
    r'|"(?:[^\\"\n]+|\\[\s\S])*(?:"|(?=\n|\Z))'
    r"|'(?:[^\\'\n]+|\\[\s\S])*(?:'|(?=\n|\Z))"
    r")"
)


def _strip_python_strings_and_comments(source_text: str) -> str:
    """Return Python source with comments and string/f-string literal text removed."""
    parts: list[str] = []
    prev_end = (1, 0)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source_text).readline):
            if tok.type in _IGNORED_PY_TOKEN_TYPES or tok.type in _SKIP_LAYOUT_TOKEN_TYPES:
                continue
            if tok.start[0] > prev_end[0]:
                parts.append("\n")
            elif tok.start[1] > prev_end[1]:
                parts.append(" ")
            parts.append(tok.string)
            prev_end = tok.end
    except (tokenize.TokenError, SyntaxError):
        return _PY_COMMENT_OR_STRING_RE.sub(" ", source_text)
    return "".join(parts)


def _check_python_db_access(imports: list[ImportRef], source_text: str) -> bool:
    for ref in imports:
        mod = ref.raw.lstrip(".")
        root = mod.split(".", 1)[0]
        if root in _DB_IMPORT_ROOTS or mod == "django.db" or mod.startswith("django.db."):
            return True
    code_text = _strip_python_strings_and_comments(source_text)
    if any(token in code_text for token in _DB_SYMBOL_PATTERNS):
        return True
    return has_sql_pattern(source_text)


class PythonExtractor(BaseExtractor):
    """Extractor for Python modules via tree-sitter with stdlib-ast and regex fallbacks."""

    language: ClassVar[Language] = Language.PYTHON

    def extract(self, parsed: ParsedSyntax, rel_path: str) -> FileExtraction:
        result = self._init_extraction(parsed, rel_path)
        if rel_path == "__main__.py" or rel_path.endswith("/__main__.py"):
            result.is_entrypoint = True

        if parsed.backend == ParserBackend.TREE_SITTER and parsed.tree is not None:
            self._extract_tree_sitter(parsed.tree.root_node, parsed.source_bytes, result)
        elif parsed.backend == ParserBackend.STDLIB_AST and parsed.python_ast is not None:
            self._extract_stdlib_ast(parsed.python_ast, parsed.source_text, result)
        else:
            if parsed.tree is not None:
                self._extract_tree_sitter(parsed.tree.root_node, parsed.source_bytes, result)
            self._augment_regex(parsed.source_text, result)

        result.has_db_access = _check_python_db_access(result.imports, parsed.source_text)
        return result

    def _extract_tree_sitter(
        self,
        root: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
    ) -> None:
        source_text = source_bytes.decode("utf-8", errors="replace")
        router_prefixes: dict[str, str] = {}
        router_frameworks: dict[str, str] = {}
        explicit_all: set[str] | None = None
        has_django_urls = False
        has_flask = "flask" in source_text.lower()

        # First pass over top-level statements for router prefixes and __all__.
        for stmt in root.children:
            if str(stmt.type) == "expression_statement":
                for child in stmt.children:
                    if str(child.type) != "assignment":
                        continue
                    left = self._field_child(child, "left")
                    right = self._field_child(child, "right")
                    if left is None or right is None:
                        continue
                    left_name = node_text(left, source_bytes).strip()
                    if left_name == "__all__" and str(right.type) in {"list", "tuple", "set"}:
                        explicit_all = {
                            strip_string_literal(node_text(item, source_bytes))
                            for item in right.children
                            if str(item.type) == "string"
                        }
                    elif str(right.type) == "call":
                        fn_node = self._field_child(right, "function")
                        args_node = self._field_child(right, "arguments")
                        fn_name = node_text(fn_node, source_bytes) if fn_node is not None else ""
                        if fn_name.endswith("APIRouter") and args_node is not None:
                            prefix = self._ts_kwarg_string(args_node, "prefix", source_bytes)
                            if prefix is not None:
                                router_prefixes[left_name] = prefix
                            router_frameworks[left_name] = "fastapi"
                        elif fn_name.endswith("Blueprint") and args_node is not None:
                            prefix = self._ts_kwarg_string(args_node, "url_prefix", source_bytes)
                            if prefix is not None:
                                router_prefixes[left_name] = prefix
                            router_frameworks[left_name] = "flask"

        # Walk all nodes for imports, complexity, __main__, and Django path() calls.
        complexity_branches = 0
        for node in iter_nodes(root):
            ntype = str(node.type)
            if ntype in _TS_COMPLEXITY_NODES:
                complexity_branches += 1

            if ntype == "import_statement":
                self._ts_parse_import(node, source_bytes, result.imports)
            elif ntype == "import_from_statement":
                self._ts_parse_import_from(node, source_bytes, result.imports)
                if any(
                    ref.raw.startswith("django.urls") or ref.raw.startswith("django.conf.urls")
                    for ref in result.imports[-1:]
                ):
                    has_django_urls = True
            elif ntype == "if_statement":
                cond = self._field_child(node, "condition")
                if cond is not None and self._is_ts_main_condition(cond, source_bytes):
                    result.is_entrypoint = True

        result.complexity = 1 + complexity_branches

        # Extract top-level classes, functions, methods, and decorated routes.
        for stmt in root.children:
            stype = str(stmt.type)
            if stype == "decorated_definition":
                self._ts_handle_decorated(
                    stmt,
                    source_bytes,
                    result,
                    router_prefixes=router_prefixes,
                    router_frameworks=router_frameworks,
                    explicit_all=explicit_all,
                    has_flask=has_flask,
                )
            elif stype == "class_definition":
                self._ts_handle_class(stmt, source_bytes, result, explicit_all=explicit_all)
            elif stype == "function_definition":
                self._ts_handle_function(
                    stmt,
                    source_bytes,
                    result,
                    kind=SymbolKind.FUNCTION,
                    explicit_all=explicit_all,
                )

        # Check Django urlpatterns / path(...) calls.
        self._ts_extract_django_routes(root, source_bytes, result, has_django_urls=has_django_urls)

    def _ts_kwarg_string(self, args_node: TsNode, kw_name: str, source_bytes: bytes) -> str | None:
        for child in args_node.children:
            if str(child.type) == "keyword_argument":
                name_node = self._field_child(child, "name")
                val_node = self._field_child(child, "value")
                if (
                    name_node is not None
                    and val_node is not None
                    and node_text(name_node, source_bytes) == kw_name
                    and str(val_node.type) == "string"
                ):
                    return strip_string_literal(node_text(val_node, source_bytes))
        return None

    def _ts_parse_import(
        self,
        node: TsNode,
        source_bytes: bytes,
        imports: list[ImportRef],
    ) -> None:
        line = int(node.start_point.row) + 1
        for child in node.children:
            ctype = str(child.type)
            if ctype == "dotted_name":
                raw = node_text(child, source_bytes).strip()
                if raw:
                    imports.append(ImportRef(raw=raw, line=line))
            elif ctype == "aliased_import":
                target = self._field_child(child, "name")
                if target is not None:
                    raw = node_text(target, source_bytes).strip()
                    if raw:
                        imports.append(ImportRef(raw=raw, line=line))

    def _ts_parse_import_from(
        self,
        node: TsNode,
        source_bytes: bytes,
        imports: list[ImportRef],
    ) -> None:
        line = int(node.start_point.row) + 1
        module_node = self._field_child(node, "module_name")
        if module_node is None:
            return

        is_relative = False
        relative_level = 0
        if str(module_node.type) == "relative_import":
            is_relative = True
            prefix_node = self._first_child_by_type(module_node, "import_prefix")
            if prefix_node is not None:
                relative_level = node_text(prefix_node, source_bytes).count(".")
            dotted_node = self._first_child_by_type(module_node, "dotted_name")
            mod_part = node_text(dotted_node, source_bytes).strip() if dotted_node else ""
            raw = f"{'.' * relative_level}{mod_part}"
        else:
            raw = node_text(module_node, source_bytes).strip()

        names: list[str] = []
        seen_import_kw = False
        for child in node.children:
            ctype = str(child.type)
            if ctype == "import":
                seen_import_kw = True
                continue
            if not seen_import_kw:
                continue
            if ctype == "dotted_name":
                names.append(node_text(child, source_bytes).strip())
            elif ctype == "aliased_import":
                target = self._field_child(child, "name")
                if target is not None:
                    names.append(node_text(target, source_bytes).strip())
            elif ctype == "wildcard_import":
                names.append("*")

        imports.append(
            ImportRef(
                raw=raw,
                names=names,
                is_relative=is_relative,
                relative_level=relative_level,
                line=line,
            )
        )

    def _is_ts_main_condition(self, cond: TsNode, source_bytes: bytes) -> bool:
        if str(cond.type) != "comparison_operator":
            return False
        parts = [node_text(c, source_bytes).strip() for c in cond.children]
        if "==" not in parts:
            return False
        cleaned = [strip_string_literal(p) if p.startswith(('"', "'")) else p for p in parts]
        return "__name__" in cleaned and "__main__" in cleaned

    def _ts_handle_class(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
        *,
        explicit_all: set[str] | None,
    ) -> None:
        name_node = self._field_child(node, "name")
        if name_node is None:
            return
        name = node_text(name_node, source_bytes).strip()
        if not name:
            return
        line = int(node.start_point.row) + 1
        end_line = max(line, int(node.end_point.row) + 1)
        exported = name in explicit_all if explicit_all is not None else not name.startswith("_")
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
        if body is None:
            return
        for member in body.children:
            mtype = str(member.type)
            if mtype == "function_definition":
                self._ts_handle_function(
                    member,
                    source_bytes,
                    result,
                    kind=SymbolKind.METHOD,
                    explicit_all=None,
                )
            elif mtype == "decorated_definition":
                defn = self._field_child(member, "definition")
                if defn is not None and str(defn.type) == "function_definition":
                    self._ts_handle_function(
                        defn,
                        source_bytes,
                        result,
                        kind=SymbolKind.METHOD,
                        explicit_all=None,
                    )

    def _ts_handle_function(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
        *,
        kind: SymbolKind,
        explicit_all: set[str] | None,
    ) -> str | None:
        name_node = self._field_child(node, "name")
        if name_node is None:
            return None
        name = node_text(name_node, source_bytes).strip()
        if not name:
            return None
        line = int(node.start_point.row) + 1
        end_line = max(line, int(node.end_point.row) + 1)
        exported = (
            name in explicit_all
            if explicit_all is not None and kind == SymbolKind.FUNCTION
            else not name.startswith("_")
        )
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
        return name

    def _ts_handle_decorated(
        self,
        node: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
        *,
        router_prefixes: dict[str, str],
        router_frameworks: dict[str, str],
        explicit_all: set[str] | None,
        has_flask: bool,
    ) -> None:
        defn = self._field_child(node, "definition")
        if defn is None:
            return
        if str(defn.type) == "class_definition":
            self._ts_handle_class(defn, source_bytes, result, explicit_all=explicit_all)
            return
        if str(defn.type) != "function_definition":
            return

        handler_name = (
            self._ts_handle_function(
                defn,
                source_bytes,
                result,
                kind=SymbolKind.FUNCTION,
                explicit_all=explicit_all,
            )
            or ""
        )

        for child in node.children:
            if str(child.type) != "decorator":
                continue
            call_node = self._first_child_by_type(child, "call")
            if call_node is None:
                continue
            fn_node = self._field_child(call_node, "function")
            args_node = self._field_child(call_node, "arguments")
            if fn_node is None or args_node is None or str(fn_node.type) != "attribute":
                continue

            obj_node = self._field_child(fn_node, "object")
            attr_node = self._field_child(fn_node, "attribute")
            if obj_node is None or attr_node is None:
                continue
            obj_name = node_text(obj_node, source_bytes).strip()
            attr_name = node_text(attr_node, source_bytes).strip().lower()
            if attr_name not in _ROUTE_METHODS:
                continue

            route_path = self._ts_first_route_path(args_node, source_bytes)
            if route_path is None:
                continue

            prefix = router_prefixes.get(obj_name, "")
            full_path = _join_route_path(prefix, route_path)
            methods = self._ts_decorator_methods(attr_name, args_node, source_bytes)
            framework = router_frameworks.get(obj_name) or (
                "flask"
                if (has_flask or obj_name in {"bp", "blueprint"} or attr_name == "route")
                else "fastapi"
            )
            line = int(child.start_point.row) + 1
            for method in methods:
                result.routes.append(
                    RouteInfo(
                        method=method,
                        path=full_path,
                        handler=handler_name,
                        line=line,
                        framework=framework,
                    )
                )

    def _ts_first_route_path(self, args_node: TsNode, source_bytes: bytes) -> str | None:
        for child in args_node.children:
            ctype = str(child.type)
            if ctype in {"(", ")", ","}:
                continue
            if ctype == "string":
                return strip_string_literal(node_text(child, source_bytes))
            if ctype == "keyword_argument":
                k_node = self._field_child(child, "name")
                v_node = self._field_child(child, "value")
                if (
                    k_node is not None
                    and v_node is not None
                    and node_text(k_node, source_bytes) in {"path", "rule"}
                    and str(v_node.type) == "string"
                ):
                    return strip_string_literal(node_text(v_node, source_bytes))
            else:
                break
        return None

    def _ts_decorator_methods(
        self,
        attr_name: str,
        args_node: TsNode,
        source_bytes: bytes,
    ) -> list[str]:
        if attr_name not in {"route", "api_route"}:
            return [attr_name.upper()]
        for child in args_node.children:
            if str(child.type) == "keyword_argument":
                k_node = self._field_child(child, "name")
                v_node = self._field_child(child, "value")
                if (
                    k_node is not None
                    and v_node is not None
                    and node_text(k_node, source_bytes) == "methods"
                ):
                    extracted = _extract_string_list_from_text(node_text(v_node, source_bytes))
                    if extracted:
                        return extracted
        return ["GET"]

    def _ts_extract_django_routes(
        self,
        root: TsNode,
        source_bytes: bytes,
        result: FileExtraction,
        *,
        has_django_urls: bool,
    ) -> None:
        for node in iter_nodes(root):
            if str(node.type) != "assignment":
                continue
            left = self._field_child(node, "left")
            right = self._field_child(node, "right")
            if left is None or right is None:
                continue
            is_urlpatterns = node_text(left, source_bytes).strip() == "urlpatterns"
            if not is_urlpatterns and not has_django_urls:
                continue
            for sub in iter_nodes(right):
                if str(sub.type) != "call":
                    continue
                fn_node = self._field_child(sub, "function")
                args_node = self._field_child(sub, "arguments")
                if fn_node is None or args_node is None:
                    continue
                fn_name = node_text(fn_node, source_bytes).strip()
                if fn_name not in {"path", "re_path", "url"} and not fn_name.endswith(
                    (".path", ".re_path")
                ):
                    continue
                pos_args = [c for c in args_node.children if str(c.type) not in {"(", ")", ","}]
                if len(pos_args) < 2 or str(pos_args[0].type) != "string":
                    continue
                route_path = strip_string_literal(node_text(pos_args[0], source_bytes))
                handler = collapse_whitespace(node_text(pos_args[1], source_bytes))
                result.routes.append(
                    RouteInfo(
                        method="ANY",
                        path=route_path,
                        handler=handler,
                        line=int(sub.start_point.row) + 1,
                        framework="django",
                    )
                )

    def _extract_stdlib_ast(
        self,
        tree: ast.Module,
        source_text: str,
        result: FileExtraction,
    ) -> None:
        lines = source_text.splitlines()
        router_prefixes: dict[str, str] = {}
        router_frameworks: dict[str, str] = {}
        explicit_all: set[str] | None = None
        has_django_urls = False
        has_flask = "flask" in source_text.lower()

        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name):
                    if target.id == "__all__" and isinstance(
                        stmt.value, (ast.List, ast.Tuple, ast.Set)
                    ):
                        explicit_all = {
                            elt.value
                            for elt in stmt.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
                    elif isinstance(stmt.value, ast.Call):
                        fn_name = self._ast_dotted_name(stmt.value.func)
                        if fn_name.endswith("APIRouter"):
                            prefix = self._ast_kw_str(stmt.value, "prefix")
                            if prefix is not None:
                                router_prefixes[target.id] = prefix
                            router_frameworks[target.id] = "fastapi"
                        elif fn_name.endswith("Blueprint"):
                            prefix = self._ast_kw_str(stmt.value, "url_prefix")
                            if prefix is not None:
                                router_prefixes[target.id] = prefix
                            router_frameworks[target.id] = "flask"

        branches = 0
        for node in ast.walk(tree):
            if isinstance(
                node,
                (
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.ExceptHandler,
                    ast.IfExp,
                    ast.BoolOp,
                ),
            ) or (hasattr(ast, "match_case") and isinstance(node, ast.match_case)):
                branches += 1
            elif isinstance(node, ast.comprehension):
                branches += len(node.ifs)

            if isinstance(node, ast.Import):
                for alias in node.names:
                    result.imports.append(ImportRef(raw=alias.name, line=node.lineno))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                is_rel = node.level > 0
                raw = f"{'.' * node.level}{mod}" if is_rel else mod
                if raw.startswith(("django.urls", "django.conf.urls")):
                    has_django_urls = True
                result.imports.append(
                    ImportRef(
                        raw=raw,
                        names=[alias.name for alias in node.names],
                        is_relative=is_rel,
                        relative_level=node.level,
                        line=node.lineno,
                    )
                )
            elif isinstance(node, ast.If) and self._is_ast_main(node.test):
                result.is_entrypoint = True

        result.complexity = 1 + branches

        for stmt in tree.body:
            if isinstance(stmt, ast.ClassDef):
                exported = (
                    stmt.name in explicit_all
                    if explicit_all is not None
                    else not stmt.name.startswith("_")
                )
                sig = lines[stmt.lineno - 1].strip() if stmt.lineno <= len(lines) else None
                result.symbols.append(
                    Symbol(
                        name=stmt.name,
                        kind=SymbolKind.CLASS,
                        line=stmt.lineno,
                        end_line=stmt.end_lineno or stmt.lineno,
                        exported=exported,
                        signature=sig,
                    )
                )
                for member in stmt.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        msig = (
                            lines[member.lineno - 1].strip()
                            if member.lineno <= len(lines)
                            else None
                        )
                        result.symbols.append(
                            Symbol(
                                name=member.name,
                                kind=SymbolKind.METHOD,
                                line=member.lineno,
                                end_line=member.end_lineno or member.lineno,
                                exported=not member.name.startswith("_"),
                                signature=msig,
                            )
                        )
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                exported = (
                    stmt.name in explicit_all
                    if explicit_all is not None
                    else not stmt.name.startswith("_")
                )
                fsig = lines[stmt.lineno - 1].strip() if stmt.lineno <= len(lines) else None
                result.symbols.append(
                    Symbol(
                        name=stmt.name,
                        kind=SymbolKind.FUNCTION,
                        line=stmt.lineno,
                        end_line=stmt.end_lineno or stmt.lineno,
                        exported=exported,
                        signature=fsig,
                    )
                )
                self._ast_extract_decorators(
                    stmt,
                    result,
                    router_prefixes=router_prefixes,
                    router_frameworks=router_frameworks,
                    has_flask=has_flask,
                )

        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "urlpatterns" for t in stmt.targets
            ):
                for sub in ast.walk(stmt.value):
                    if isinstance(sub, ast.Call):
                        fn_name = self._ast_dotted_name(sub.func)
                        if fn_name in {"path", "re_path", "url"} and len(sub.args) >= 2:
                            first = sub.args[0]
                            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                                handler = (
                                    ast.unparse(sub.args[1]) if hasattr(ast, "unparse") else ""
                                )
                                result.routes.append(
                                    RouteInfo(
                                        method="ANY",
                                        path=first.value,
                                        handler=handler,
                                        line=sub.lineno,
                                        framework="django",
                                    )
                                )
        if has_django_urls and not result.routes:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn_name = self._ast_dotted_name(node.func)
                    if fn_name in {"path", "re_path"} and len(node.args) >= 2:
                        first = node.args[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            handler = ast.unparse(node.args[1]) if hasattr(ast, "unparse") else ""
                            result.routes.append(
                                RouteInfo(
                                    method="ANY",
                                    path=first.value,
                                    handler=handler,
                                    line=node.lineno,
                                    framework="django",
                                )
                            )

    def _ast_extract_decorators(
        self,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        result: FileExtraction,
        *,
        router_prefixes: dict[str, str],
        router_frameworks: dict[str, str],
        has_flask: bool,
    ) -> None:
        for dec in fn.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            attr_name = dec.func.attr.lower()
            if attr_name not in _ROUTE_METHODS:
                continue
            obj_name = self._ast_dotted_name(dec.func.value)
            route_path: str | None = None
            if (
                dec.args
                and isinstance(dec.args[0], ast.Constant)
                and isinstance(dec.args[0].value, str)
            ):
                route_path = dec.args[0].value
            else:
                route_path = self._ast_kw_str(dec, "path") or self._ast_kw_str(dec, "rule")
            if route_path is None:
                continue

            methods = [attr_name.upper()]
            if attr_name in {"route", "api_route"}:
                methods = self._ast_kw_methods(dec) or ["GET"]
            prefix = router_prefixes.get(obj_name, "")
            full_path = _join_route_path(prefix, route_path)
            framework = router_frameworks.get(obj_name) or (
                "flask"
                if (has_flask or obj_name in {"bp", "blueprint"} or attr_name == "route")
                else "fastapi"
            )
            for method in methods:
                result.routes.append(
                    RouteInfo(
                        method=method,
                        path=full_path,
                        handler=fn.name,
                        line=dec.lineno,
                        framework=framework,
                    )
                )

    @staticmethod
    def _ast_dotted_name(node: ast.AST) -> str:
        parts: list[str] = []
        current: Any = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))

    @staticmethod
    def _ast_kw_str(call: ast.Call, name: str) -> str | None:
        for kw in call.keywords:
            if (
                kw.arg == name
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                return kw.value.value
        return None

    @staticmethod
    def _ast_kw_methods(call: ast.Call) -> list[str]:
        for kw in call.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                out = [
                    elt.value.upper()
                    for elt in kw.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
                if out:
                    return out
        return []

    @staticmethod
    def _is_ast_main(test: ast.AST) -> bool:
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
            return False
        if not isinstance(test.ops[0], ast.Eq):
            return False
        left, right = test.left, test.comparators[0]
        is_name_main = (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        )
        is_main_name = (
            isinstance(right, ast.Name)
            and right.id == "__name__"
            and isinstance(left, ast.Constant)
            and left.value == "__main__"
        )
        return is_name_main or is_main_name

    def _augment_regex(self, source_text: str, result: FileExtraction) -> None:
        seen_imports = {(imp.raw, imp.line) for imp in result.imports}
        seen_symbols = {(sym.name, sym.line) for sym in result.symbols}
        seen_routes = {(r.method, r.path, r.line) for r in result.routes}

        for match in _PY_IMPORT_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            for part in match.group(1).split(","):
                mod = part.strip().split(" as ")[0].strip()
                if mod and (mod, line) not in seen_imports:
                    seen_imports.add((mod, line))
                    result.imports.append(ImportRef(raw=mod, line=line))

        for match in _PY_FROM_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            raw = match.group(1).strip()
            names_raw = match.group(2).strip("() ")
            names = [p.strip().split(" as ")[0].strip() for p in names_raw.split(",") if p.strip()]
            level = len(raw) - len(raw.lstrip("."))
            if raw and (raw, line) not in seen_imports:
                seen_imports.add((raw, line))
                result.imports.append(
                    ImportRef(
                        raw=raw,
                        names=names,
                        is_relative=level > 0,
                        relative_level=level,
                        line=line,
                    )
                )

        for match in _PY_CLASS_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            name = match.group(2)
            if (name, line) not in seen_symbols:
                seen_symbols.add((name, line))
                result.symbols.append(
                    Symbol(
                        name=name,
                        kind=SymbolKind.CLASS,
                        line=line,
                        end_line=line,
                        exported=not name.startswith("_"),
                    )
                )

        for match in _PY_DEF_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            indent = match.group(1)
            name = match.group(2)
            kind = SymbolKind.METHOD if indent else SymbolKind.FUNCTION
            if (name, line) not in seen_symbols:
                seen_symbols.add((name, line))
                result.symbols.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        line=line,
                        end_line=line,
                        exported=not name.startswith("_"),
                    )
                )

        for match in _PY_ROUTE_DECORATOR_RE.finditer(source_text):
            line = source_text.count("\n", 0, match.start()) + 1
            verb = match.group(2).upper()
            method = "GET" if verb in {"ROUTE", "API_ROUTE"} else verb
            path = strip_string_literal(match.group(3))
            if (method, path, line) not in seen_routes:
                seen_routes.add((method, path, line))
                result.routes.append(
                    RouteInfo(method=method, path=path, line=line, framework="fastapi")
                )

        if _PY_MAIN_RE.search(source_text):
            result.is_entrypoint = True

        if result.complexity <= 1:
            branches = len(re.findall(r"\b(?:if|elif|for|while|except|case|and|or)\b", source_text))
            result.complexity = 1 + branches
