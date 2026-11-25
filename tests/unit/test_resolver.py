"""Unit tests for the cross-language import and dependency resolver."""

from __future__ import annotations

from pathlib import Path

from codeatlas.scanner.extractors import extract_file, extract_text
from codeatlas.scanner.parser_registry import ParserRegistry
from codeatlas.scanner.resolver import resolve_dependencies
from codeatlas.scanner.walker import walk_repository


def test_resolve_python_relative_and_dotted_imports(
    tmp_path: Path,
    parser_registry: ParserRegistry,
) -> None:
    files = {
        "src/mypkg/__init__.py": "from .utils import clean\n",
        "src/mypkg/utils.py": "def clean(v: str) -> str:\n    return v.strip()\n",
        "backend/db.py": "def connect(): pass\n",
        "backend/services/__init__.py": "",
        "backend/services/auth.py": (
            "from fastapi import APIRouter\n"
            "from ..db import connect\n"
            "from mypkg.utils import clean\n"
            "from .missing import nope\n"
        ),
        "backend/routes.py": "from services.auth import connect\n",
    }
    extractions = {}
    for rel, code in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        extractions[rel] = extract_text(code, path=rel, registry=parser_registry)

    deps = resolve_dependencies(tmp_path, extractions)
    edges = {(e.source, e.target): e.symbols for e in deps.internal_edges}

    assert edges[("src/mypkg/__init__.py", "src/mypkg/utils.py")] == ["clean"]
    assert edges[("backend/services/auth.py", "backend/db.py")] == ["connect"]
    assert edges[("backend/services/auth.py", "src/mypkg/utils.py")] == ["clean"]
    assert edges[("backend/routes.py", "backend/services/auth.py")] == ["connect"]

    # Unresolved relative import `.missing` must NOT pollute external_deps.
    assert deps.external_deps_by_node["backend/services/auth.py"] == ["fastapi"]


def test_resolve_js_ts_aliases_jsonc_and_index_files(
    tmp_path: Path,
    parser_registry: ParserRegistry,
) -> None:
    tsconfig = """\
{
  // JSONC line comment
  /* Block comment */
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"],
    },
  },
}
"""
    (tmp_path / "frontend").mkdir(parents=True, exist_ok=True)
    (tmp_path / "frontend" / "tsconfig.json").write_text(tsconfig, encoding="utf-8")

    files = {
        "frontend/src/types/index.ts": "export type User = { id: string };\n",
        "frontend/src/api/client.ts": "export const ping = () => 'pong';\n",
        "frontend/src/pages/Home.tsx": (
            'import React from "react";\n'
            'import { QueryClient } from "@tanstack/react-query";\n'
            'import type { User } from "@/types";\n'
            'import { ping } from "../api/client.js";\n'
            'import { nope } from "@/missing/mod";\n'
        ),
    }
    extractions = {}
    for rel, code in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
        extractions[rel] = extract_text(code, path=rel, registry=parser_registry)

    deps = resolve_dependencies(tmp_path, extractions)
    edges = {(e.source, e.target): e.symbols for e in deps.internal_edges}

    assert edges[("frontend/src/pages/Home.tsx", "frontend/src/types/index.ts")] == ["User"]
    assert edges[("frontend/src/pages/Home.tsx", "frontend/src/api/client.ts")] == ["ping"]
    assert deps.external_deps_by_node["frontend/src/pages/Home.tsx"] == [
        "@tanstack/react-query",
        "react",
    ]


def test_resolve_php_psr4_and_relative_requires(
    tmp_path: Path,
    parser_registry: ParserRegistry,
) -> None:
    composer = """\
{
  "autoload": {
    "psr-4": {
      "Acme\\\\Worker\\\\": "src/"
    }
  }
}
"""
    (tmp_path / "worker").mkdir(parents=True, exist_ok=True)
    (tmp_path / "worker" / "composer.json").write_text(composer, encoding="utf-8")

    files = {
        "worker/src/helpers.php": "<?php\nfunction help(): void {}\n",
        "worker/src/Services/Mailer.php": (
            "<?php\nnamespace Acme\\Worker\\Services;\nclass Mailer {}\n"
        ),
        "worker/src/Models/Payment.php": (
            "<?php\n"
            "namespace Acme\\Worker\\Models;\n"
            "use Illuminate\\Support\\Facades\\DB;\n"
            "use Acme\\Worker\\Services\\Mailer;\n"
            "require_once __DIR__ . '/../helpers.php';\n"
            "class Payment {}\n"
        ),
    }
    extractions = {}
    for rel, code in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
        extractions[rel] = extract_text(code, path=rel, registry=parser_registry)

    deps = resolve_dependencies(tmp_path, extractions)
    edges = {(e.source, e.target): e.symbols for e in deps.internal_edges}

    assert edges[("worker/src/Models/Payment.php", "worker/src/Services/Mailer.php")] == ["Mailer"]
    assert edges[("worker/src/Models/Payment.php", "worker/src/helpers.php")] == []
    assert deps.external_deps_by_node["worker/src/Models/Payment.php"] == [
        "Illuminate\\Support\\Facades\\DB"
    ]


def test_resolve_go_module_packages_and_no_self_edges(
    tmp_path: Path,
    parser_registry: ParserRegistry,
) -> None:
    (tmp_path / "go.mod").write_text("module github.com/acme/atlas\n\ngo 1.22\n", encoding="utf-8")
    files = {
        "internal/store/repo.go": "package store\nfunc Get() {}\n",
        "internal/store/queries.go": "package store\nfunc List() {}\n",
        "internal/store/repo_test.go": (
            'package store\nimport "testing"\nfunc TestGet(t *testing.T) {}\n'
        ),
        "cmd/api/main.go": (
            "package main\n"
            "import (\n"
            '    "fmt"\n'
            '    "github.com/gin-gonic/gin"\n'
            '    "github.com/acme/atlas/internal/store"\n'
            ")\n"
            "func main() { fmt.Println(); store.Get(); _ = gin.Default() }\n"
        ),
    }
    extractions = {}
    for rel, code in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
        extractions[rel] = extract_text(code, path=rel, registry=parser_registry)

    deps = resolve_dependencies(tmp_path, extractions)
    targets_from_main = [e.target for e in deps.internal_edges if e.source == "cmd/api/main.go"]

    assert targets_from_main == [
        "internal/store/queries.go",
        "internal/store/repo.go",
    ]
    assert all(e.source != e.target for e in deps.internal_edges)
    assert deps.external_deps_by_node["cmd/api/main.go"] == [
        "fmt",
        "github.com/gin-gonic/gin",
    ]


def test_resolve_sample_saas_repository(
    sample_saas_root: Path,
    parser_registry: ParserRegistry,
) -> None:
    walked = walk_repository(sample_saas_root, supported_only=True)
    extractions = {wf.relative_path: extract_file(wf, parser_registry) for wf in walked.files}
    deps = resolve_dependencies(sample_saas_root, extractions)

    edge_pairs = {(e.source, e.target) for e in deps.internal_edges}
    # Deterministic sort check
    assert [(e.source, e.target) for e in deps.internal_edges] == sorted(edge_pairs)

    # Intentional cycle between billing_service and invoice_service
    assert ("backend/billing_service.py", "backend/invoice_service.py") in edge_pairs
    assert ("backend/invoice_service.py", "backend/billing_service.py") in edge_pairs

    # Intentional PHP layer violation PaymentRecord -> NotificationService
    assert (
        "worker/src/Models/PaymentRecord.php",
        "worker/src/Services/NotificationService.php",
    ) in edge_pairs

    # TS @/ alias resolution
    assert (
        "frontend/src/pages/BillingPage.tsx",
        "frontend/src/components/PlanCard.tsx",
    ) in edge_pairs

    # Dead/orphaned module has zero incoming internal edges
    assert all(e.target != "backend/legacy_exporter.py" for e in deps.internal_edges)
