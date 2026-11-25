"""Deterministic architectural layer classification using POSIX paths and AST cues."""

from __future__ import annotations

import posixpath
import re

from codeatlas.models.config import Language, Layer
from codeatlas.models.graph import normalize_posix_rel_path

_TOKEN_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]+|\d+")

_TEST_DIRS: frozenset[str] = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "spec",
        "specs",
        "e2e",
        "unit_tests",
        "integration_tests",
        "testing",
    }
)

_TEST_EXACT_SUFFIXES: tuple[str, ...] = (
    "Test.php",
    "TestCase.php",
    "Test.ts",
    "Test.tsx",
    "Test.js",
    "Test.jsx",
    "Spec.ts",
    "Spec.tsx",
    "Spec.js",
    "Spec.jsx",
    "Test.go",
)

_CONFIG_DIRS: frozenset[str] = frozenset(
    {
        "config",
        "configs",
        "conf",
        "settings",
        "infra",
        "infrastructure",
        "deploy",
        "deployment",
        "helm",
        "k8s",
        "kubernetes",
        "terraform",
        ".github",
        "docker",
    }
)

_CONFIG_STEMS: frozenset[str] = frozenset(
    {
        "config",
        "configs",
        "settings",
        "configuration",
        "tsconfig",
        "jsconfig",
        "composer",
        "package",
        "pyproject",
        "setup",
        "env",
        "dotenv",
        "webpack",
        "rollup",
        "babel",
        "postcss",
        "tailwind",
        "vitest",
        "pytest",
    }
)

_CONFIG_TRAILING_TOKENS: frozenset[str] = frozenset(
    {"config", "configs", "settings", "configuration"}
)

_ENTRYPOINT_DIRS: frozenset[str] = frozenset(
    {
        "routes",
        "route",
        "routers",
        "router",
        "endpoints",
        "entrypoints",
        "cmd",
        "cli",
        "bin",
    }
)

_ENTRYPOINT_STEMS: frozenset[str] = frozenset(
    {"main", "__main__", "cli", "wsgi", "asgi", "manage", "server", "app"}
)

_ENTRYPOINT_TRAILING_TOKENS: frozenset[str] = frozenset(
    {"route", "routes", "router", "routers", "endpoint", "endpoints", "cli"}
)

_FRONTEND_DIRS: frozenset[str] = frozenset(
    {
        "frontend",
        "ui",
        "components",
        "component",
        "pages",
        "page",
        "views",
        "view",
        "layouts",
        "layout",
        "screens",
        "screen",
        "widgets",
        "styles",
    }
)

_FRONTEND_JS_DIRS: frozenset[str] = frozenset({"web", "client", "hooks", "templates"})

_FRONTEND_EXTS: frozenset[str] = frozenset({".tsx", ".jsx", ".vue", ".svelte", ".astro"})

_FRONTEND_TRAILING_TOKENS: frozenset[str] = frozenset(
    {"component", "components", "page", "pages", "view", "views", "widget", "widgets", "layout"}
)

_DATA_DIRS: frozenset[str] = frozenset(
    {
        "models",
        "model",
        "repositories",
        "repository",
        "repos",
        "dao",
        "dal",
        "db",
        "database",
        "migrations",
        "migration",
        "alembic",
        "entities",
        "entity",
        "orm",
        "persistence",
        "storage",
        "store",
        "stores",
        "schemas",
    }
)

_DATA_TOKENS: frozenset[str] = frozenset(
    {
        "model",
        "models",
        "repository",
        "repositories",
        "repo",
        "dao",
        "dal",
        "db",
        "database",
        "migration",
        "migrations",
        "entity",
        "entities",
        "orm",
        "schema",
        "schemas",
        "store",
        "persistence",
    }
)

_SERVICE_DIRS: frozenset[str] = frozenset(
    {
        "services",
        "service",
        "controllers",
        "controller",
        "jobs",
        "job",
        "workers",
        "worker",
        "tasks",
        "task",
        "usecases",
        "use_cases",
        "interactors",
        "handlers",
        "handler",
        "processors",
        "queues",
        "cron",
        "workflows",
        "managers",
        "engines",
    }
)

_SERVICE_TOKENS: frozenset[str] = frozenset(
    {
        "service",
        "services",
        "controller",
        "controllers",
        "job",
        "jobs",
        "worker",
        "workers",
        "task",
        "tasks",
        "usecase",
        "interactor",
        "processor",
        "manager",
        "workflow",
        "orchestrator",
    }
)

_SHARED_DIRS: frozenset[str] = frozenset(
    {
        "utils",
        "util",
        "types",
        "core",
        "lib",
        "libs",
        "common",
        "shared",
        "helpers",
        "helper",
        "constants",
    }
)

_SHARED_TOKENS: frozenset[str] = frozenset(
    {
        "utils",
        "util",
        "types",
        "core",
        "lib",
        "common",
        "shared",
        "helper",
        "helpers",
        "constants",
        "logger",
        "logging",
        "crypto",
    }
)


def _tokenize(text: str) -> tuple[str, ...]:
    """Split snake_case, kebab-case, dot-separated, and CamelCase identifiers."""
    return tuple(match.lower() for match in _TOKEN_RE.findall(text))


def _is_test_path(
    dirs_lower: tuple[str, ...],
    filename: str,
    filename_lower: str,
    stem_lower: str,
) -> bool:
    """Return True when path cues identify an automated test or fixture file."""
    if any(part in _TEST_DIRS for part in dirs_lower):
        return True
    if filename_lower == "conftest.py":
        return True
    if filename_lower.startswith(("test_", "spec_")):
        return True
    if stem_lower.endswith(("_test", "_spec")) or stem_lower in {"test", "tests", "spec", "specs"}:
        return True
    if ".test." in filename_lower or ".spec." in filename_lower:
        return True
    return filename.endswith(_TEST_EXACT_SUFFIXES)


def _is_config_path(
    dirs_lower: tuple[str, ...],
    filename_lower: str,
    stem_lower: str,
    stem_tokens: tuple[str, ...],
) -> bool:
    """Return True when path cues identify configuration or infrastructure files."""
    if any(part in _CONFIG_DIRS for part in dirs_lower):
        return True
    if ".config." in filename_lower:
        return True
    if stem_lower in _CONFIG_STEMS:
        return True
    return bool(stem_tokens) and stem_tokens[-1] in _CONFIG_TRAILING_TOKENS


def _is_frontend_context(
    dirs_lower: tuple[str, ...],
    ext_lower: str,
    language: Language,
    stem_tokens: tuple[str, ...],
) -> bool:
    """Return True when path/extension cues indicate UI or frontend client code."""
    if ext_lower in _FRONTEND_EXTS:
        return True
    if any(part in _FRONTEND_DIRS for part in dirs_lower):
        return True
    is_js_ts = language in {Language.TYPESCRIPT, Language.JAVASCRIPT}
    if is_js_ts and any(part in _FRONTEND_JS_DIRS for part in dirs_lower):
        return True
    return is_js_ts and bool(stem_tokens) and stem_tokens[-1] in _FRONTEND_TRAILING_TOKENS


def _is_entrypoint_path(
    dirs_lower: tuple[str, ...],
    ext_lower: str,
    stem_lower: str,
    stem_tokens: tuple[str, ...],
    in_frontend_context: bool,
) -> bool:
    """Return True when path cues identify a server, CLI, or API route entrypoint."""
    if "pages" in dirs_lower and "api" in dirs_lower:
        return True
    if "api" in dirs_lower and stem_lower == "route":
        return True
    if any(part in _ENTRYPOINT_DIRS for part in dirs_lower):
        return True
    if (
        "api" in dirs_lower
        and not in_frontend_context
        and ext_lower not in _FRONTEND_EXTS
        and stem_lower not in {"client", "types", "schemas"}
    ):
        return True
    if bool(stem_tokens) and stem_tokens[-1] in _ENTRYPOINT_TRAILING_TOKENS:
        return True
    return (
        not in_frontend_context
        and ext_lower not in _FRONTEND_EXTS
        and stem_lower in _ENTRYPOINT_STEMS
    )


def _classify_data_or_service(
    dirs_lower: tuple[str, ...],
    stem_tokens: tuple[str, ...],
) -> Layer | None:
    """Resolve Data vs Service layer using innermost-first path specificity."""
    if stem_tokens:
        tail = stem_tokens[-1]
        if tail in _DATA_TOKENS:
            return Layer.DATA
        if tail in _SERVICE_TOKENS:
            return Layer.SERVICE

        has_data_token = any(tok in _DATA_TOKENS for tok in stem_tokens)
        has_service_token = any(tok in _SERVICE_TOKENS for tok in stem_tokens)
        if has_data_token and not has_service_token:
            return Layer.DATA
        if has_service_token and not has_data_token:
            return Layer.SERVICE

    for directory in reversed(dirs_lower):
        if directory in _DATA_DIRS:
            return Layer.DATA
        if directory in _SERVICE_DIRS:
            return Layer.SERVICE
    return None


def classify_layer(
    path: str,
    language: Language,
    *,
    is_entrypoint: bool = False,
    has_routes: bool = False,
    has_db_access: bool = False,
) -> Layer:
    """Classify a source file into one of the 7 architectural layers deterministically.

    Precedence:
    1. ``test`` (test directories and filename conventions override all AST cues).
    2. ``config`` (config/infra directories and config file patterns).
    3. ``entrypoint`` (``is_entrypoint`` / ``has_routes`` AST cues or route/CLI paths).
    4. ``frontend`` (UI components, pages, TSX/JSX, and frontend workspace paths).
    5. ``data`` vs ``service`` (innermost path specificity, then ``has_db_access`` -> ``data``).
    6. ``shared`` (utils/types/core/lib and default fallback).
    """
    normalized = normalize_posix_rel_path(path)
    if not normalized or normalized == ".":
        return Layer.SHARED

    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return Layer.SHARED

    dirs_lower = tuple(part.lower() for part in parts[:-1])
    filename = parts[-1]
    filename_lower = filename.lower()
    stem, ext = posixpath.splitext(filename)
    stem_lower = stem.lower()
    ext_lower = ext.lower()
    stem_tokens = _tokenize(stem)

    if _is_test_path(dirs_lower, filename, filename_lower, stem_lower):
        return Layer.TEST

    if _is_config_path(dirs_lower, filename_lower, stem_lower, stem_tokens):
        return Layer.CONFIG

    if is_entrypoint or has_routes:
        return Layer.ENTRYPOINT

    in_frontend_context = _is_frontend_context(dirs_lower, ext_lower, language, stem_tokens)

    if _is_entrypoint_path(
        dirs_lower,
        ext_lower,
        stem_lower,
        stem_tokens,
        in_frontend_context,
    ):
        return Layer.ENTRYPOINT

    if in_frontend_context:
        return Layer.FRONTEND

    specific_layer = _classify_data_or_service(dirs_lower, stem_tokens)
    if specific_layer is not None:
        return specific_layer

    if has_db_access:
        return Layer.DATA

    if any(part in _SHARED_DIRS for part in dirs_lower) or any(
        tok in _SHARED_TOKENS for tok in stem_tokens
    ):
        return Layer.SHARED

    return Layer.SHARED
