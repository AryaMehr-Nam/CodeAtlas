"""Static UI assets loader for CodeAtlas web studio and standalone HTML exports."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

UI_DIR: Path = Path(__file__).resolve().parent

UI_ASSETS: tuple[str, ...] = (
    "index.html",
    "styles.css",
    "layouts.js",
    "canvas_engine.js",
    "app.js",
)

UI_CONTENT_TYPES: dict[str, str] = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "layouts.js": "application/javascript; charset=utf-8",
    "canvas_engine.js": "application/javascript; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}


def get_ui_dir() -> Path:
    """Return the filesystem directory containing the bundled UI assets."""
    return UI_DIR


def read_ui_asset(filename: str) -> str:
    """Read a whitelisted UI asset file as UTF-8 text."""
    if filename not in UI_ASSETS:
        raise KeyError(f"Unknown CodeAtlas UI asset: {filename}")

    package_name = __package__ or "codeatlas.ui"
    try:
        asset_ref = resources.files(package_name).joinpath(filename)
        if asset_ref.is_file():
            return asset_ref.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        pass

    fallback_path = UI_DIR / filename
    return fallback_path.read_text(encoding="utf-8")


def load_ui_bundle() -> dict[str, str]:
    """Load all bundled UI assets mapped by filename."""
    return {name: read_ui_asset(name) for name in UI_ASSETS}


__all__ = [
    "UI_ASSETS",
    "UI_CONTENT_TYPES",
    "UI_DIR",
    "get_ui_dir",
    "load_ui_bundle",
    "read_ui_asset",
]
