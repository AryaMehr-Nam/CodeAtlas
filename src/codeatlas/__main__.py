"""Entrypoint for ``python -m codeatlas``."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from typing import cast


def main() -> None:
    """Load and run the CodeAtlas Typer CLI application."""
    try:
        cli_module = importlib.import_module("codeatlas.cli")
    except ModuleNotFoundError as exc:
        if exc.name == "codeatlas.cli":
            print("codeatlas.cli is not available in this installation.", file=sys.stderr)
            raise SystemExit(1) from exc
        raise

    app = getattr(cli_module, "app", None)
    if not callable(app):
        print("codeatlas.cli.app is missing or not callable.", file=sys.stderr)
        raise SystemExit(1)

    cli_app = cast(Callable[[], None], app)
    cli_app()


if __name__ == "__main__":
    main()
