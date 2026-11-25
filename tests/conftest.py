"""Shared pytest fixtures for CodeAtlas unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.scanner.parser_registry import ParserRegistry


@pytest.fixture
def parser_registry() -> ParserRegistry:
    """Return a fresh tree-sitter enabled ``ParserRegistry``."""
    return ParserRegistry(enable_tree_sitter=True)


@pytest.fixture
def sample_saas_root() -> Path:
    """Return the path to the bundled ``demo/sample_saas`` repository."""
    return Path(__file__).resolve().parents[1] / "demo" / "sample_saas"
