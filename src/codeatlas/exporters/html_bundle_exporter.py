"""Self-contained, offline, CSP-hardened HTML bundle exporter."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import quote

from codeatlas.exporters._common import normalize_newlines
from codeatlas.models.analysis import AnalysisResult
from codeatlas.models.config import ThemeMode
from codeatlas.ui import load_ui_bundle

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round">'
    '<polygon points="12 2 21 7 12 12 3 7 12 2"/>'
    '<polyline points="3 12 12 17 21 12"/>'
    '<polyline points="3 17 12 22 21 17"/>'
    "</svg>"
)
_FAVICON_DATA_URI = "data:image/svg+xml," + quote(_FAVICON_SVG, safe="")

_HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_DATA_THEME_ATTR_RE = re.compile(r'\bdata-theme=["\'][^"\']*["\']', re.IGNORECASE)
_CSP_META_RE = re.compile(
    r'<meta\b[^>]*\bhttp-equiv=["\']Content-Security-Policy["\'][^>]*>',
    re.IGNORECASE,
)
_CHARSET_META_RE = re.compile(r'<meta\b[^>]*\bcharset=["\'][^"\']*["\'][^>]*>', re.IGNORECASE)
_TITLE_TAG_RE = re.compile(r"<title\b[^>]*>.*?</title>", re.IGNORECASE | re.DOTALL)
_FAVICON_LINK_RE = re.compile(
    r'<link\b[^>]*\bhref=["\'](?:/static/|/)?favicon\.svg["\'][^>]*>',
    re.IGNORECASE,
)
_ICON_REL_RE = re.compile(r'<link\b[^>]*\brel=["\'](?:shortcut\s+)?icon["\']', re.IGNORECASE)
_STYLESHEET_LINK_RE = re.compile(
    r'<link\b[^>]*\bhref=["\'](?:/static/)?styles\.css["\'][^>]*>',
    re.IGNORECASE,
)
_LAYOUTS_SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bsrc=["\'](?:/static/)?layouts\.js["\'][^>]*>\s*</script>',
    re.IGNORECASE,
)
_CANVAS_SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bsrc=["\'](?:/static/)?canvas_engine\.js["\'][^>]*>\s*</script>',
    re.IGNORECASE,
)
_APP_SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bsrc=["\'](?:/static/)?app\.js["\'][^>]*>\s*</script>',
    re.IGNORECASE,
)
_BODY_CLOSE_RE = re.compile(r"</body>", re.IGNORECASE)


def _sha256_csp_token(normalized_text: str) -> str:
    """Compute a CSP Level 2/3 ``'sha256-<base64>'`` token for LF-normalized text."""
    digest = hashlib.sha256(normalized_text.encode("utf-8")).digest()
    encoded = base64.b64encode(digest).decode("ascii")
    return f"'sha256-{encoded}'"


def _set_html_theme(html: str, theme_mode: ThemeMode) -> str:
    theme_value = ThemeMode(theme_mode).value

    def _replace_html(match: re.Match[str]) -> str:
        tag = match.group(0)
        if _DATA_THEME_ATTR_RE.search(tag):
            return _DATA_THEME_ATTR_RE.sub(f'data-theme="{theme_value}"', tag, count=1)
        return tag[:-1] + f' data-theme="{theme_value}">'

    return _HTML_TAG_RE.sub(_replace_html, html, count=1)


def _inject_csp_meta(html: str, csp_meta: str) -> str:
    if _CSP_META_RE.search(html):
        return _CSP_META_RE.sub(lambda _m: csp_meta, html, count=1)
    if _CHARSET_META_RE.search(html):
        return _CHARSET_META_RE.sub(
            lambda m: f"{m.group(0)}\n  {csp_meta}",
            html,
            count=1,
        )
    return re.sub(
        r"<head\b[^>]*>",
        lambda m: f"{m.group(0)}\n  {csp_meta}",
        html,
        count=1,
        flags=re.IGNORECASE,
    )


def _inline_favicon(html: str) -> str:
    icon_tag = f'<link rel="icon" type="image/svg+xml" href="{_FAVICON_DATA_URI}">'
    if _FAVICON_LINK_RE.search(html):
        return _FAVICON_LINK_RE.sub(
            lambda m: re.sub(
                r'href=["\'](?:/static/|/)?favicon\.svg["\']',
                f'href="{_FAVICON_DATA_URI}"',
                m.group(0),
                count=1,
                flags=re.IGNORECASE,
            ),
            html,
            count=1,
        )
    if _ICON_REL_RE.search(html):
        return html
    if _TITLE_TAG_RE.search(html):
        return _TITLE_TAG_RE.sub(
            lambda m: f"{m.group(0)}\n  {icon_tag}",
            html,
            count=1,
        )
    return html


def _replace_exactly_once(
    html: str,
    pattern: re.Pattern[str],
    replacement: str,
    asset_name: str,
) -> str:
    updated, count = pattern.subn(lambda _m: replacement, html, count=1)
    if count != 1:
        raise RuntimeError(f"Expected tag for UI asset '{asset_name}' in index.html")
    return updated


def _serialize_bootstrap_json(result: AnalysisResult) -> str:
    payload = {
        "graph": result.graph.model_dump(mode="json"),
        "health": result.health.model_dump(mode="json"),
        "watch_enabled": False,
        "static_mode": True,
        "repo_name": result.graph.repo_name,
    }
    raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return raw_json.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def export_html_bundle(
    result: AnalysisResult,
    *,
    default_theme: ThemeMode = ThemeMode.OBSIDIAN,
) -> str:
    """Export a self-contained offline HTML studio bundle with SHA-256 CSP hashes."""
    bundle = load_ui_bundle()

    html = normalize_newlines(bundle["index.html"])
    styles_css = normalize_newlines(bundle["styles.css"])
    layouts_js = normalize_newlines(bundle["layouts.js"])
    canvas_js = normalize_newlines(bundle["canvas_engine.js"])
    app_js = normalize_newlines(bundle["app.js"])

    style_hash = _sha256_csp_token(styles_css)
    script_hashes = [
        _sha256_csp_token(layouts_js),
        _sha256_csp_token(canvas_js),
        _sha256_csp_token(app_js),
    ]

    csp_content = (
        "default-src 'none'; "
        f"script-src {' '.join(script_hashes)}; "
        f"style-src {style_hash}; "
        "img-src 'self' data: blob:; "
        "connect-src 'none'; "
        "base-uri 'none'"
    )
    csp_meta = f'<meta http-equiv="Content-Security-Policy" content="{csp_content}">'

    html = _set_html_theme(html, default_theme)
    html = _inject_csp_meta(html, csp_meta)
    html = _inline_favicon(html)

    html = _replace_exactly_once(
        html, _STYLESHEET_LINK_RE, f"<style>{styles_css}</style>", "styles.css"
    )
    html = _replace_exactly_once(
        html, _LAYOUTS_SCRIPT_RE, f"<script>{layouts_js}</script>", "layouts.js"
    )
    html = _replace_exactly_once(
        html, _CANVAS_SCRIPT_RE, f"<script>{canvas_js}</script>", "canvas_engine.js"
    )
    html = _replace_exactly_once(html, _APP_SCRIPT_RE, f"<script>{app_js}</script>", "app.js")

    bootstrap_json = _serialize_bootstrap_json(result)
    bootstrap_tag = (
        f'<script id="codeatlas-bootstrap" type="application/json">{bootstrap_json}</script>'
    )
    html = _replace_exactly_once(html, _BODY_CLOSE_RE, f"  {bootstrap_tag}\n</body>", "</body>")

    normalized_html = normalize_newlines(html)
    if not normalized_html.endswith("\n"):
        normalized_html += "\n"
    return normalized_html
