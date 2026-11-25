"""Unit tests for language extractors (Python, JS/TS/TSX, PHP, Go) and fallbacks."""

from __future__ import annotations

from pathlib import Path

from codeatlas.models.config import Language
from codeatlas.models.graph import SymbolKind
from codeatlas.scanner.extractors import extract_file, extract_text
from codeatlas.scanner.parser_registry import ParserBackend, ParserRegistry
from codeatlas.scanner.walker import walk_repository


def test_python_extractor_ast_details(parser_registry: ParserRegistry) -> None:
    source = """\
# Top-level module comment
import os, sys as _sys
from sqlalchemy import select
from .auth_service import authenticate_user, register_user as reg
from ..models import User

router = APIRouter(prefix="/api/v1")

class _InternalHelper:
    def _secret(self) -> None:
        pass

    def run(self, x: int) -> int:
        if x > 10:
            return x
        elif x > 0 and x <= 10:
            return x + 1
        return 0

@router.post("/items")
async def create_item(flag: bool) -> str:
    for i in range(2):
        try:
            pass
        except ValueError:
            pass
    return "ok" if flag else "no"

if __name__ == "__main__":
    print("boot")
"""
    ext = extract_text(source, path="backend/routes.py", registry=parser_registry)

    assert ext.backend == ParserBackend.TREE_SITTER
    assert ext.warnings == []
    assert ext.loc == 30
    assert ext.comment_lines == 1
    assert ext.code_lines == 24
    assert ext.is_entrypoint is True
    assert ext.has_db_access is True
    assert ext.complexity >= 8

    import_map = {imp.raw: imp for imp in ext.imports}
    assert "os" in import_map
    assert "sys" in import_map
    assert import_map[".auth_service"].is_relative is True
    assert import_map[".auth_service"].relative_level == 1
    assert import_map[".auth_service"].names == ["authenticate_user", "register_user"]
    assert import_map["..models"].is_relative is True
    assert import_map["..models"].relative_level == 2

    sym_map = {(s.name, s.kind): s for s in ext.symbols}
    assert sym_map[("_InternalHelper", SymbolKind.CLASS)].exported is False
    assert sym_map[("_secret", SymbolKind.METHOD)].exported is False
    assert sym_map[("run", SymbolKind.METHOD)].exported is True
    assert sym_map[("create_item", SymbolKind.FUNCTION)].exported is True
    assert sym_map[("create_item", SymbolKind.FUNCTION)].end_line > (
        sym_map[("create_item", SymbolKind.FUNCTION)].line
    )

    assert len(ext.routes) == 1
    route = ext.routes[0]
    assert route.method == "POST"
    assert route.path == "/api/v1/items"
    assert route.handler == "create_item"
    assert route.framework == "fastapi"


def test_python_django_and_flask_routes(parser_registry: ParserRegistry) -> None:
    flask_src = """\
from flask import Blueprint
bp = Blueprint("users", __name__, url_prefix="/users")

@bp.route("/profile", methods=["GET", "POST"])
def profile():
    return "ok"
"""
    flask_ext = extract_text(flask_src, path="app/users.py", registry=parser_registry)
    assert [(r.method, r.path, r.framework) for r in flask_ext.routes] == [
        ("GET", "/users/profile", "flask"),
        ("POST", "/users/profile", "flask"),
    ]

    django_src = """\
from django.urls import path
from . import views

urlpatterns = [
    path("orders/<int:pk>/", views.order_detail),
]
"""
    django_ext = extract_text(django_src, path="shop/urls.py", registry=parser_registry)
    assert len(django_ext.routes) == 1
    assert django_ext.routes[0].path == "orders/<int:pk>/"
    assert django_ext.routes[0].handler == "views.order_detail"
    assert django_ext.routes[0].framework == "django"


def test_python_db_access_ignores_strings_and_comments(parser_registry: ParserRegistry) -> None:
    non_db_literal_src = """\
# References like session.execute or models.Model in comments must not trigger.
_DB_SYMBOL_PATTERNS = ("session.execute", "DeclarativeBase")


def describe(name: str) -> str:
    \"\"\"Docstring mentioning sessionmaker, create_engine, and cursor.execute.\"\"\"
    return f"template: session.execute {name} DeclarativeBase"
"""
    non_db_ext = extract_text(
        non_db_literal_src, path="scanner/patterns.py", registry=parser_registry
    )
    assert non_db_ext.has_db_access is False

    malformed_non_db_src = 'def broken(:\n    return "session.execute"\n# DeclarativeBase\n'
    malformed_ext = extract_text(
        malformed_non_db_src, path="scanner/broken_patterns.py", registry=parser_registry
    )
    assert malformed_ext.has_db_access is False

    session_call_src = """\
def load_users(session):
    return session.execute("SELECT 1")
"""
    assert (
        extract_text(session_call_src, path="repo/users.py", registry=parser_registry).has_db_access
        is True
    )

    django_model_src = """\
class Foo(models.Model):
    pass
"""
    assert (
        extract_text(django_model_src, path="app/models.py", registry=parser_registry).has_db_access
        is True
    )

    sqlalchemy_import_src = """\
import sqlalchemy
"""
    assert (
        extract_text(
            sqlalchemy_import_src, path="app/db.py", registry=parser_registry
        ).has_db_access
        is True
    )

    sql_literal_src = """\
def get_query() -> str:
    return "SELECT id FROM accounts"
"""
    assert (
        extract_text(sql_literal_src, path="app/queries.py", registry=parser_registry).has_db_access
        is True
    )

    fstring_expr_src = """\
def debug_run(session, stmt) -> str:
    return f"{session.execute(stmt)}"
"""
    assert (
        extract_text(fstring_expr_src, path="app/debug.py", registry=parser_registry).has_db_access
        is True
    )


def test_js_ts_tsx_extractor(parser_registry: ParserRegistry) -> None:
    tsx_src = """\
// UI billing widget
/* multi
   line */
import type { PlanTier } from "@/types/billing";
import React, { useState } from "react";
export { ApiClientError } from "../api/client";
const helper = require("./helper");
const lazyMod = import("../lazy");

export interface CardProps {
  tier: PlanTier;
}
export type Currency = "USD" | "EUR";

export class Formatter {
  format(val: number): string {
    return val > 0 ? `$${val}` : "$0";
  }
}

export function PlanWidget({ tier }: CardProps): JSX.Element {
  const [ok, setOk] = useState(true);
  if (ok && tier === "growth") {
    return <div>Growth</div>;
  }
  return <span>{String(ok)}</span>;
}
"""
    ext = extract_text(tsx_src, path="src/components/PlanWidget.tsx", registry=parser_registry)
    assert ext.backend == ParserBackend.TREE_SITTER
    assert ext.language == Language.TYPESCRIPT
    assert ext.warnings == []
    assert ext.comment_lines == 3
    assert ext.has_db_access is False
    assert ext.complexity >= 4

    raws = {imp.raw for imp in ext.imports}
    assert raws == {"@/types/billing", "react", "../api/client", "./helper", "../lazy"}

    sym_map = {s.name: s for s in ext.symbols}
    assert sym_map["CardProps"].kind == SymbolKind.INTERFACE
    assert sym_map["CardProps"].exported is True
    assert sym_map["Currency"].kind == SymbolKind.TYPE
    assert sym_map["Formatter"].kind == SymbolKind.CLASS
    assert sym_map["format"].kind == SymbolKind.METHOD
    assert sym_map["PlanWidget"].kind == SymbolKind.COMPONENT
    assert sym_map["PlanWidget"].exported is True

    express_src = """\
import { PrismaClient } from "@prisma/client";
const prisma = new PrismaClient();
const lookup = new Map();
lookup.get("not-a-route");
app.get("/api/users", listUsers);
fastify.route({ method: "POST", url: "/api/orders", handler: createOrder });
"""
    api_ext = extract_text(express_src, path="server/routes.ts", registry=parser_registry)
    assert api_ext.has_db_access is True
    assert [(r.method, r.path, r.handler) for r in api_ext.routes] == [
        ("GET", "/api/users", "listUsers"),
        ("POST", "/api/orders", "createOrder"),
    ]

    next_app_src = """\
export async function GET() { return Response.json({ ok: true }); }
export async function POST() { return Response.json({ created: true }); }
"""
    next_app_ext = extract_text(
        next_app_src,
        path="frontend/app/(public)/api/orders/route.ts",
        registry=parser_registry,
    )
    assert [(r.method, r.path, r.framework) for r in next_app_ext.routes] == [
        ("GET", "/api/orders", "nextjs"),
        ("POST", "/api/orders", "nextjs"),
    ]

    next_pages_src = "export default function handler(req, res) { res.end('ok'); }\n"
    next_pages_ext = extract_text(
        next_pages_src,
        path="pages/api/health.js",
        registry=parser_registry,
    )
    assert [(r.method, r.path, r.framework) for r in next_pages_ext.routes] == [
        ("ANY", "/api/health", "nextjs")
    ]


def test_php_extractor(parser_registry: ParserRegistry) -> None:
    php_src = """\
<?php

declare(strict_types=1);

namespace App\\Http\\Controllers;

use App\\Models\\{User, Order as CustomerOrder};
use Illuminate\\Support\\Facades\\DB;
require_once __DIR__ . '/helpers.php';

interface Auditable {
    public function auditId(): string;
}

trait Timestamps {
    protected function touch(): void {}
}

#[Route('/api')]
class UserController extends Model
{
    #[Route('/users', methods: ['GET', 'POST'])]
    public function index(bool $active): array
    {
        if ($active) {
            return DB::select('SELECT * FROM users');
        } elseif (!$active) {
            return [];
        }
        return [];
    }

    private function normalize(): void {}
}

function helper_fn(): int { return 1; }

Route::post('/webhooks/stripe', [UserController::class, 'index']);
"""
    ext = extract_text(
        php_src,
        path="app/Http/Controllers/UserController.php",
        registry=parser_registry,
    )

    assert ext.backend == ParserBackend.TREE_SITTER
    assert ext.warnings == []
    assert ext.package_or_namespace == "App\\Http\\Controllers"
    assert ext.has_db_access is True
    assert ext.complexity >= 3

    imp_map = {imp.raw: imp for imp in ext.imports}
    assert "App\\Models\\User" in imp_map
    assert "App\\Models\\Order" in imp_map
    assert "Illuminate\\Support\\Facades\\DB" in imp_map
    assert imp_map["./helpers.php"].is_relative is True

    sym_map = {(s.name, s.kind): s for s in ext.symbols}
    assert ("Auditable", SymbolKind.INTERFACE) in sym_map
    assert ("Timestamps", SymbolKind.TRAIT) in sym_map
    assert ("UserController", SymbolKind.CLASS) in sym_map
    assert ("helper_fn", SymbolKind.FUNCTION) in sym_map
    assert sym_map[("index", SymbolKind.METHOD)].exported is True
    assert sym_map[("normalize", SymbolKind.METHOD)].exported is False

    routes = {(r.method, r.path, r.framework) for r in ext.routes}
    assert ("GET", "/api/users", "symfony") in routes
    assert ("POST", "/api/users", "symfony") in routes
    assert ("POST", "/webhooks/stripe", "laravel") in routes


def test_go_extractor(parser_registry: ParserRegistry) -> None:
    go_src = """\
package main

import "fmt"
import (
    "database/sql"
    mux "net/http"
    "github.com/acme/saas/internal/billing"
)

type Service interface {
    Charge(amount int) error
}

type server struct {
    db *sql.DB
}

func (s *server) HandleCharge(ok bool) int {
    if ok {
        return 1
    }
    for i := 0; i < 3; i++ {
        fmt.Println(i)
    }
    return 0
}

func main() {
    mux.HandleFunc("GET /healthz", nil)
    r.POST("/api/charge", nil)
}
"""
    ext = extract_text(go_src, path="cmd/api/main.go", registry=parser_registry)

    assert ext.backend == ParserBackend.TREE_SITTER
    assert ext.warnings == []
    assert ext.package_or_namespace == "main"
    assert ext.is_entrypoint is True
    assert ext.has_db_access is True
    assert ext.complexity >= 3

    imp_map = {imp.raw: imp for imp in ext.imports}
    assert set(imp_map.keys()) == {
        "fmt",
        "database/sql",
        "net/http",
        "github.com/acme/saas/internal/billing",
    }
    assert imp_map["net/http"].names == ["mux"]

    sym_map = {(s.name, s.kind): s for s in ext.symbols}
    assert sym_map[("Service", SymbolKind.INTERFACE)].exported is True
    assert sym_map[("server", SymbolKind.STRUCT)].exported is False
    assert sym_map[("HandleCharge", SymbolKind.METHOD)].exported is True
    assert sym_map[("main", SymbolKind.FUNCTION)].exported is False

    assert [(r.method, r.path, r.framework) for r in ext.routes] == [
        ("GET", "/healthz", "net/http"),
        ("POST", "/api/charge", "gin"),
    ]


def test_fallbacks_and_malformed_file_resilience(tmp_path: Path) -> None:
    # 1. stdlib-ast fallback when tree-sitter is disabled.
    no_ts_registry = ParserRegistry(enable_tree_sitter=False)
    py_ok = "import json\ndef greet(name: str) -> str:\n    return name\n"
    py_ext = extract_text(py_ok, path="pkg/ok.py", registry=no_ts_registry)
    assert py_ext.backend == ParserBackend.STDLIB_AST
    assert len(py_ext.warnings) == 1
    assert py_ext.warnings[0].fallback_used == ParserBackend.STDLIB_AST.value
    assert [imp.raw for imp in py_ext.imports] == ["json"]
    assert [sym.name for sym in py_ext.symbols] == ["greet"]

    # 2. Malformed syntax across all languages must never crash and must emit warnings.
    ts_registry = ParserRegistry(enable_tree_sitter=True)
    malformed_samples = [
        ("broken.py", "import os\ndef broken(:\n    return\n"),
        ("broken.ts", "import { x } from './x';\nexport function oops( { return;\n"),
        ("broken.php", "<?php\nnamespace App;\nuse Foo\\Bar;\nclass Broken {\n"),
        ("broken.go", 'package main\nimport "fmt"\nfunc broken( {\n'),
    ]
    for rel_path, content in malformed_samples:
        bad_file = tmp_path / rel_path
        bad_file.write_bytes(content.encode("utf-8") + b"\xff\xfe")
        walked = walk_repository(tmp_path, supported_only=True)
        wf = next(item for item in walked.files if item.relative_path == rel_path)
        extracted = extract_file(wf, ts_registry)
        assert extracted.backend == ParserBackend.REGEX_FALLBACK
        assert len(extracted.warnings) >= 1
        assert extracted.loc >= 2
        assert extracted.imports  # still recovers valid top-of-file imports


def test_extract_sample_saas_repo(
    sample_saas_root: Path,
    parser_registry: ParserRegistry,
) -> None:
    walked = walk_repository(sample_saas_root, supported_only=True)
    extractions = {wf.relative_path: extract_file(wf, parser_registry) for wf in walked.files}

    assert len(extractions) == 22
    assert all(ext.backend == ParserBackend.TREE_SITTER for ext in extractions.values())
    assert all(not ext.warnings for ext in extractions.values())

    analytics = extractions["backend/analytics_service.py"]
    assert analytics.complexity >= 25
    assert analytics.has_db_access is True

    auth_routes = extractions["backend/auth_routes.py"]
    assert len(auth_routes.routes) == 4
    assert auth_routes.routes[0].path.startswith("/api/auth/")

    worker_routes = extractions["worker/src/Routes/worker.php"]
    assert [(r.method, r.path) for r in worker_routes.routes] == [
        ("POST", "/worker/sync"),
        ("GET", "/worker/health"),
    ]
