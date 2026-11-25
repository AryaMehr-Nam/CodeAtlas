"""Cross-language import and dependency resolver for extracted source files."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from codeatlas.models.config import Language
from codeatlas.models.graph import Edge, ImportRef, normalize_posix_rel_path
from codeatlas.scanner.extractors.base import FileExtraction

_JS_TS_EXTENSIONS: tuple[str, ...] = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
)
_TS_FROM_JS_MAP: dict[str, tuple[str, ...]] = {
    ".js": (".ts", ".tsx", ".js", ".jsx"),
    ".jsx": (".tsx", ".jsx", ".ts"),
    ".mjs": (".mts", ".mjs"),
    ".cjs": (".cts", ".cjs"),
}
_PY_EXTENSIONS: tuple[str, ...] = (".py", ".pyw")
_PHP_EXTENSIONS: tuple[str, ...] = (".php", ".phtml")
_GO_MODULE_RE = re.compile(r'^\s*module\s+("([^"]+)"|(\S+))', re.MULTILINE)


@dataclass(slots=True)
class ResolvedDependencies:
    """Resolved internal module edges and external dependencies per node."""

    internal_edges: list[Edge] = field(default_factory=list)
    external_deps_by_node: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _TsConfig:
    """Parsed TypeScript/JavaScript path-resolution configuration."""

    dir_rel: str
    base_dir_rel: str
    has_explicit_base_url: bool
    paths: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class _ComposerConfig:
    """Parsed PHP Composer PSR-4 autoload mappings."""

    dir_rel: str
    psr4_prefixes: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class _GoModConfig:
    """Parsed Go module declaration."""

    dir_rel: str
    module_name: str


def _strip_jsonc(raw_text: str) -> str:
    """Strip ``//`` and ``/* ... */`` comments and trailing commas from JSONC safely."""
    out: list[str] = []
    idx = 0
    length = len(raw_text)
    in_string = False

    while idx < length:
        ch = raw_text[idx]
        if in_string:
            out.append(ch)
            if ch == "\\" and idx + 1 < length:
                out.append(raw_text[idx + 1])
                idx += 2
                continue
            if ch == '"':
                in_string = False
            idx += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            idx += 1
            continue

        if raw_text.startswith("//", idx):
            newline_idx = raw_text.find("\n", idx)
            if newline_idx == -1:
                break
            idx = newline_idx
            continue

        if raw_text.startswith("/*", idx):
            end_idx = raw_text.find("*/", idx + 2)
            if end_idx == -1:
                break
            idx = end_idx + 2
            continue

        out.append(ch)
        idx += 1

    # Remove trailing commas outside string literals before '}' or ']'.
    no_comments = "".join(out)
    cleaned: list[str] = []
    idx = 0
    length = len(no_comments)
    in_string = False
    while idx < length:
        ch = no_comments[idx]
        if in_string:
            cleaned.append(ch)
            if ch == "\\" and idx + 1 < length:
                cleaned.append(no_comments[idx + 1])
                idx += 2
                continue
            if ch == '"':
                in_string = False
            idx += 1
            continue
        if ch == '"':
            in_string = True
            cleaned.append(ch)
            idx += 1
            continue
        if ch == ",":
            look = idx + 1
            while look < length and no_comments[look].isspace():
                look += 1
            if look < length and no_comments[look] in {"}", "]"}:
                idx += 1
                continue
        cleaned.append(ch)
        idx += 1

    return "".join(cleaned)


def _dir_ancestors(dir_rel: str) -> list[str]:
    """Return ``dir_rel`` and all ancestor directories up to repo root ``''``."""
    cleaned = "" if dir_rel in {"", "."} else dir_rel.strip("/")
    if not cleaned:
        return [""]
    parts = cleaned.split("/")
    ancestors = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    ancestors.append("")
    return ancestors


def _join_rel(base_dir: str, rel_target: str) -> str | None:
    """Normalize ``base_dir / rel_target`` in POSIX space; return None if escaping root."""
    joined = rel_target if not base_dir else posixpath.join(base_dir, rel_target)
    normed = posixpath.normpath(joined)
    if normed == ".":
        return ""
    if normed == ".." or normed.startswith("../"):
        return None
    return normed.lstrip("/")


def _load_tsconfig_file(
    config_path: Path,
    dir_rel: str,
    root_path: Path,
    seen_files: set[Path],
) -> dict[str, object]:
    resolved = config_path.resolve()
    if resolved in seen_files or not config_path.is_file():
        return {}
    seen_files.add(resolved)
    try:
        raw = config_path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(_strip_jsonc(raw))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    merged_opts: dict[str, object] = {}
    extends_val = data.get("extends")
    if isinstance(extends_val, str) and extends_val.startswith("."):
        parent_cfg = (config_path.parent / extends_val).with_suffix(".json")
        if not parent_cfg.is_file():
            parent_cfg = config_path.parent / extends_val
        try:
            parent_resolved = parent_cfg.resolve()
            if parent_resolved.is_relative_to(root_path.resolve()):
                parent_opts = _load_tsconfig_file(parent_cfg, dir_rel, root_path, seen_files)
                merged_opts.update(parent_opts)
        except OSError:
            pass

    compiler_opts = data.get("compilerOptions")
    if isinstance(compiler_opts, dict):
        merged_opts.update(compiler_opts)
    return merged_opts


def _parse_tsconfig(root_path: Path, dir_rel: str) -> _TsConfig | None:
    cfg_dir = root_path / dir_rel if dir_rel else root_path
    cfg_file = cfg_dir / "tsconfig.json"
    if not cfg_file.is_file():
        cfg_file = cfg_dir / "jsconfig.json"
        if not cfg_file.is_file():
            return None

    opts = _load_tsconfig_file(cfg_file, dir_rel, root_path, set())
    base_url_val = opts.get("baseUrl")
    has_explicit_base = isinstance(base_url_val, str) and bool(base_url_val.strip())
    base_url_str = base_url_val.strip() if isinstance(base_url_val, str) else "."
    base_dir = _join_rel(dir_rel, base_url_str)
    if base_dir is None:
        base_dir = dir_rel

    raw_paths = opts.get("paths")
    parsed_paths: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(raw_paths, dict):
        for pattern, targets in raw_paths.items():
            if not isinstance(pattern, str) or not isinstance(targets, list):
                continue
            str_targets = tuple(t for t in targets if isinstance(t, str) and t.strip())
            if str_targets:
                parsed_paths.append((pattern, str_targets))
        parsed_paths.sort(key=lambda item: len(item[0]), reverse=True)

    return _TsConfig(
        dir_rel=dir_rel,
        base_dir_rel=base_dir,
        has_explicit_base_url=has_explicit_base,
        paths=tuple(parsed_paths),
    )


def _parse_composer(root_path: Path, dir_rel: str) -> _ComposerConfig | None:
    cfg_dir = root_path / dir_rel if dir_rel else root_path
    cfg_file = cfg_dir / "composer.json"
    if not cfg_file.is_file():
        return None
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    prefixes: dict[str, list[str]] = {}
    for section_name in ("autoload", "autoload-dev"):
        section = data.get(section_name)
        if not isinstance(section, dict):
            continue
        psr4 = section.get("psr-4")
        if not isinstance(psr4, dict):
            continue
        for ns_prefix, mapped_dirs in psr4.items():
            if not isinstance(ns_prefix, str):
                continue
            clean_ns = ns_prefix.lstrip("\\")
            if clean_ns and not clean_ns.endswith("\\"):
                clean_ns = f"{clean_ns}\\"
            dir_list = [mapped_dirs] if isinstance(mapped_dirs, str) else mapped_dirs
            if not isinstance(dir_list, list):
                continue
            for mapped in dir_list:
                if isinstance(mapped, str):
                    joined = _join_rel(dir_rel, mapped.rstrip("/"))
                    if joined is not None:
                        prefixes.setdefault(clean_ns, []).append(joined)

    if not prefixes:
        return None
    sorted_items = tuple(
        sorted(
            ((ns, tuple(dirs)) for ns, dirs in prefixes.items()),
            key=lambda item: len(item[0]),
            reverse=True,
        )
    )
    return _ComposerConfig(dir_rel=dir_rel, psr4_prefixes=sorted_items)


def _parse_go_mod(root_path: Path, dir_rel: str) -> _GoModConfig | None:
    cfg_dir = root_path / dir_rel if dir_rel else root_path
    mod_file = cfg_dir / "go.mod"
    if not mod_file.is_file():
        return None
    try:
        raw = mod_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cleaned_lines = [line.split("//", 1)[0] for line in raw.splitlines()]
    match = _GO_MODULE_RE.search("\n".join(cleaned_lines))
    if not match:
        return None
    mod_name = (match.group(2) or match.group(3) or "").strip()
    if not mod_name:
        return None
    return _GoModConfig(dir_rel=dir_rel, module_name=mod_name)


class _DependencyResolver:
    """Internal stateful resolver caching repo config and index lookups."""

    def __init__(self, root_path: Path, extractions: dict[str, FileExtraction]) -> None:
        self.root_path = root_path
        self.extractions = {
            normalize_posix_rel_path(path): ext for path, ext in extractions.items()
        }
        self.known_files = set(self.extractions.keys())

        self.tsconfig_cache: dict[str, _TsConfig | None] = {}
        self.composer_cache: dict[str, _ComposerConfig | None] = {}
        self.gomod_cache: dict[str, _GoModConfig | None] = {}

        # Directory -> sorted list of non-test Go file paths inside that package directory.
        self.go_pkg_files: dict[str, list[str]] = {}
        for path, ext in self.extractions.items():
            if ext.language == Language.GO and not path.endswith("_test.go"):
                pkg_dir = posixpath.dirname(path)
                if pkg_dir == ".":
                    pkg_dir = ""
                self.go_pkg_files.setdefault(pkg_dir, []).append(path)
        for files in self.go_pkg_files.values():
            files.sort()

        self.all_composer_configs: list[_ComposerConfig] = []
        self._preload_configs()

    def _preload_configs(self) -> None:
        seen_php_dirs: set[str] = set()
        for path, ext in self.extractions.items():
            dir_rel = posixpath.dirname(path)
            if dir_rel == ".":
                dir_rel = ""
            if ext.language in {Language.JAVASCRIPT, Language.TYPESCRIPT}:
                for anc in _dir_ancestors(dir_rel):
                    if anc not in self.tsconfig_cache:
                        self.tsconfig_cache[anc] = _parse_tsconfig(self.root_path, anc)
            elif ext.language == Language.PHP:
                for anc in _dir_ancestors(dir_rel):
                    if anc not in self.composer_cache:
                        cfg = _parse_composer(self.root_path, anc)
                        self.composer_cache[anc] = cfg
                        if cfg is not None and anc not in seen_php_dirs:
                            seen_php_dirs.add(anc)
                            self.all_composer_configs.append(cfg)
            elif ext.language == Language.GO:
                for anc in _dir_ancestors(dir_rel):
                    if anc not in self.gomod_cache:
                        self.gomod_cache[anc] = _parse_go_mod(self.root_path, anc)

    def resolve_all(self) -> ResolvedDependencies:
        edge_symbols: dict[tuple[str, str], set[str]] = {}
        external_deps: dict[str, set[str]] = {path: set() for path in self.extractions}

        for source_path in sorted(self.extractions):
            ext = self.extractions[source_path]
            file_dir = posixpath.dirname(source_path)
            if file_dir == ".":
                file_dir = ""

            for ref in ext.imports:
                if ext.language == Language.PYTHON:
                    self._resolve_python_ref(
                        source_path, file_dir, ref, edge_symbols, external_deps
                    )
                elif ext.language in {Language.JAVASCRIPT, Language.TYPESCRIPT}:
                    self._resolve_js_ts_ref(source_path, file_dir, ref, edge_symbols, external_deps)
                elif ext.language == Language.PHP:
                    self._resolve_php_ref(source_path, file_dir, ref, edge_symbols, external_deps)
                elif ext.language == Language.GO:
                    self._resolve_go_ref(source_path, file_dir, ref, edge_symbols, external_deps)

        internal_edges = [
            Edge(
                source=src,
                target=dst,
                symbols=sorted(syms),
                kind="import",
            )
            for (src, dst), syms in sorted(edge_symbols.items())
        ]
        external_deps_by_node = {path: sorted(deps) for path, deps in sorted(external_deps.items())}
        return ResolvedDependencies(
            internal_edges=internal_edges,
            external_deps_by_node=external_deps_by_node,
        )

    def _add_edge(
        self,
        edge_symbols: dict[tuple[str, str], set[str]],
        source: str,
        target: str,
        symbols: list[str],
    ) -> None:
        if target == source or target not in self.known_files:
            return
        bucket = edge_symbols.setdefault((source, target), set())
        for sym in symbols:
            if sym and sym != "*":
                bucket.add(sym)

    def _probe_python_module(self, base_dir: str, module_name: str) -> str | None:
        mod_rel = module_name.replace(".", "/").strip("/")
        if not mod_rel:
            for ext in _PY_EXTENSIONS:
                init_candidate = (
                    posixpath.join(base_dir, f"__init__{ext}") if base_dir else f"__init__{ext}"
                )
                if init_candidate in self.known_files:
                    return init_candidate
            return None

        joined = _join_rel(base_dir, mod_rel)
        if joined is None:
            return None
        for ext in _PY_EXTENSIONS:
            file_candidate = f"{joined}{ext}"
            if file_candidate in self.known_files:
                return file_candidate
        for ext in _PY_EXTENSIONS:
            init_candidate = posixpath.join(joined, f"__init__{ext}")
            if init_candidate in self.known_files:
                return init_candidate
        return None

    def _resolve_python_ref(
        self,
        source_path: str,
        file_dir: str,
        ref: ImportRef,
        edge_symbols: dict[tuple[str, str], set[str]],
        external_deps: dict[str, set[str]],
    ) -> None:
        raw = ref.raw.strip()
        if not raw and not ref.is_relative:
            return

        if ref.is_relative or raw.startswith("."):
            level = (
                ref.relative_level if ref.relative_level > 0 else (len(raw) - len(raw.lstrip(".")))
            )
            up_steps = max(level - 1, 0)
            parts = [p for p in file_dir.split("/") if p]
            if up_steps > len(parts):
                return
            base_dir = "/".join(parts[: len(parts) - up_steps]) if up_steps else file_dir
            mod_part = raw.lstrip(".")

            if mod_part:
                target = self._probe_python_module(base_dir, mod_part)
                if target is not None:
                    if target.endswith(("__init__.py", "__init__.pyw")) and ref.names:
                        remaining: list[str] = []
                        for name in ref.names:
                            sub_target = self._probe_python_module(base_dir, f"{mod_part}.{name}")
                            if sub_target is not None:
                                self._add_edge(edge_symbols, source_path, sub_target, [name])
                            else:
                                remaining.append(name)
                        if remaining:
                            self._add_edge(edge_symbols, source_path, target, remaining)
                    else:
                        self._add_edge(edge_symbols, source_path, target, ref.names)
                    return

                # Handle `from .pkg import submod` when pkg has no __init__.py (namespace pkg).
                for name in ref.names:
                    sub_target = self._probe_python_module(base_dir, f"{mod_part}.{name}")
                    if sub_target is not None:
                        self._add_edge(edge_symbols, source_path, sub_target, [name])
                return

            # `from . import foo, bar`
            remaining = []
            for name in ref.names:
                sub_target = self._probe_python_module(base_dir, name)
                if sub_target is not None:
                    self._add_edge(edge_symbols, source_path, sub_target, [name])
                else:
                    remaining.append(name)
            if remaining:
                pkg_init = self._probe_python_module(base_dir, "")
                if pkg_init is not None:
                    self._add_edge(edge_symbols, source_path, pkg_init, remaining)
            return

        # Absolute/dotted Python import: check repo root, src/, ancestors/subproject roots.
        ancestors = _dir_ancestors(file_dir)
        candidate_roots: list[str] = ["", "src"]
        for anc in reversed(ancestors):
            if anc not in candidate_roots:
                candidate_roots.append(anc)
            anc_src = posixpath.join(anc, "src") if anc else "src"
            if anc_src not in candidate_roots:
                candidate_roots.append(anc_src)

        for root_cand in candidate_roots:
            target = self._probe_python_module(root_cand, raw)
            if target is not None:
                if target.endswith(("__init__.py", "__init__.pyw")) and ref.names:
                    remaining = []
                    for name in ref.names:
                        sub_target = self._probe_python_module(root_cand, f"{raw}.{name}")
                        if sub_target is not None:
                            self._add_edge(edge_symbols, source_path, sub_target, [name])
                        else:
                            remaining.append(name)
                    if remaining:
                        self._add_edge(edge_symbols, source_path, target, remaining)
                else:
                    self._add_edge(edge_symbols, source_path, target, ref.names)
                return

            # Namespace package fallback (`from app.services import billing` without __init__.py)
            if ref.names:
                matched_any = False
                for name in ref.names:
                    sub_target = self._probe_python_module(root_cand, f"{raw}.{name}")
                    if sub_target is not None:
                        matched_any = True
                        self._add_edge(edge_symbols, source_path, sub_target, [name])
                if matched_any:
                    return

        top_pkg = raw.split(".", 1)[0]
        if top_pkg:
            external_deps[source_path].add(top_pkg)

    def _probe_js_ts_target(self, candidate_rel: str) -> str | None:
        if candidate_rel in self.known_files:
            return candidate_rel

        _, ext = posixpath.splitext(candidate_rel)
        if ext in _TS_FROM_JS_MAP:
            stem = candidate_rel[: -len(ext)]
            for mapped_ext in _TS_FROM_JS_MAP[ext]:
                mapped = f"{stem}{mapped_ext}"
                if mapped in self.known_files:
                    return mapped

        for suffix in _JS_TS_EXTENSIONS:
            file_cand = f"{candidate_rel}{suffix}"
            if file_cand in self.known_files:
                return file_cand

        for suffix in _JS_TS_EXTENSIONS:
            idx_cand = posixpath.join(candidate_rel, f"index{suffix}")
            if idx_cand in self.known_files:
                return idx_cand

        return None

    def _nearest_tsconfig(self, file_dir: str) -> _TsConfig | None:
        for anc in _dir_ancestors(file_dir):
            cfg = self.tsconfig_cache.get(anc)
            if cfg is not None:
                return cfg
        return None

    def _resolve_js_ts_ref(
        self,
        source_path: str,
        file_dir: str,
        ref: ImportRef,
        edge_symbols: dict[tuple[str, str], set[str]],
        external_deps: dict[str, set[str]],
    ) -> None:
        raw = ref.raw.strip()
        if not raw:
            return

        if ref.is_relative or raw.startswith("."):
            candidate = _join_rel(file_dir, raw)
            if candidate is not None:
                target = self._probe_js_ts_target(candidate)
                if target is not None:
                    self._add_edge(edge_symbols, source_path, target, ref.names)
            return

        cfg = self._nearest_tsconfig(file_dir)
        matched_alias = False
        if cfg is not None:
            for pattern, targets in cfg.paths:
                matched_wildcard = self._match_ts_pattern(pattern, raw)
                if matched_wildcard is None:
                    continue
                matched_alias = True
                for target_pattern in targets:
                    substituted = (
                        target_pattern.replace("*", matched_wildcard, 1)
                        if "*" in target_pattern
                        else target_pattern
                    )
                    candidate = _join_rel(cfg.base_dir_rel, substituted)
                    if candidate is not None:
                        resolved = self._probe_js_ts_target(candidate)
                        if resolved is not None:
                            self._add_edge(edge_symbols, source_path, resolved, ref.names)
                            return

            if cfg.has_explicit_base_url:
                candidate = _join_rel(cfg.base_dir_rel, raw)
                if candidate is not None:
                    resolved = self._probe_js_ts_target(candidate)
                    if resolved is not None:
                        self._add_edge(edge_symbols, source_path, resolved, ref.names)
                        return

        if matched_alias:
            return

        # External npm dependency: preserve @scope/package or bare package root.
        parts = raw.split("/")
        pkg_name = f"{parts[0]}/{parts[1]}" if raw.startswith("@") and len(parts) >= 2 else parts[0]
        if pkg_name:
            external_deps[source_path].add(pkg_name)

    @staticmethod
    def _match_ts_pattern(pattern: str, specifier: str) -> str | None:
        if "*" not in pattern:
            return "" if specifier == pattern else None
        prefix, suffix = pattern.split("*", 1)
        if not specifier.startswith(prefix) or not specifier.endswith(suffix):
            return None
        end_idx = len(specifier) - len(suffix) if suffix else len(specifier)
        if end_idx < len(prefix):
            return None
        return specifier[len(prefix) : end_idx]

    def _probe_php_path(self, candidate_rel: str) -> str | None:
        if candidate_rel in self.known_files:
            return candidate_rel
        for ext in _PHP_EXTENSIONS:
            cand = f"{candidate_rel}{ext}"
            if cand in self.known_files:
                return cand
        return None

    def _composer_candidates(self, file_dir: str) -> list[_ComposerConfig]:
        ordered: list[_ComposerConfig] = []
        seen: set[str] = set()
        for anc in _dir_ancestors(file_dir):
            cfg = self.composer_cache.get(anc)
            if cfg is not None and anc not in seen:
                seen.add(anc)
                ordered.append(cfg)
        for cfg in self.all_composer_configs:
            if cfg.dir_rel not in seen:
                seen.add(cfg.dir_rel)
                ordered.append(cfg)
        return ordered

    def _resolve_php_ref(
        self,
        source_path: str,
        file_dir: str,
        ref: ImportRef,
        edge_symbols: dict[tuple[str, str], set[str]],
        external_deps: dict[str, set[str]],
    ) -> None:
        raw = ref.raw.strip()
        if not raw:
            return

        if ref.is_relative or raw.startswith("."):
            candidate = _join_rel(file_dir, raw)
            if candidate is not None:
                target = self._probe_php_path(candidate)
                if target is not None:
                    self._add_edge(edge_symbols, source_path, target, ref.names)
            return

        fqn = raw.lstrip("\\")
        for cfg in self._composer_candidates(file_dir):
            for ns_prefix, base_dirs in cfg.psr4_prefixes:
                if fqn.startswith(ns_prefix):
                    rel_class = fqn[len(ns_prefix) :].replace("\\", "/")
                    for base_dir in base_dirs:
                        candidate = _join_rel(base_dir, rel_class)
                        if candidate is not None:
                            target = self._probe_php_path(candidate)
                            if target is not None:
                                self._add_edge(edge_symbols, source_path, target, ref.names)
                                return

        # Fallback: direct PSR-0 / path match from repo root or subproject roots.
        rel_fqn = fqn.replace("\\", "/")
        for root_cand in ("", "src", *_dir_ancestors(file_dir)):
            candidate = _join_rel(root_cand, rel_fqn)
            if candidate is not None:
                target = self._probe_php_path(candidate)
                if target is not None:
                    self._add_edge(edge_symbols, source_path, target, ref.names)
                    return

        external_deps[source_path].add(fqn)

    def _nearest_go_mod(self, file_dir: str) -> _GoModConfig | None:
        for anc in _dir_ancestors(file_dir):
            cfg = self.gomod_cache.get(anc)
            if cfg is not None:
                return cfg
        return None

    def _resolve_go_ref(
        self,
        source_path: str,
        file_dir: str,
        ref: ImportRef,
        edge_symbols: dict[tuple[str, str], set[str]],
        external_deps: dict[str, set[str]],
    ) -> None:
        raw = ref.raw.strip()
        if not raw:
            return

        target_dir: str | None = None
        if ref.is_relative or raw.startswith("."):
            target_dir = _join_rel(file_dir, raw)
        else:
            go_mod = self._nearest_go_mod(file_dir)
            if go_mod is not None:
                if raw == go_mod.module_name:
                    target_dir = go_mod.dir_rel
                elif raw.startswith(f"{go_mod.module_name}/"):
                    sub = raw[len(go_mod.module_name) + 1 :]
                    target_dir = _join_rel(go_mod.dir_rel, sub)

        if target_dir is not None:
            pkg_files = self.go_pkg_files.get(target_dir, [])
            for target_file in pkg_files:
                if target_file != source_path:
                    self._add_edge(edge_symbols, source_path, target_file, ref.names)
            return

        if not ref.is_relative and not raw.startswith("."):
            external_deps[source_path].add(raw)


def resolve_dependencies(
    root_path: str | Path,
    extractions: dict[str, FileExtraction],
) -> ResolvedDependencies:
    """Resolve extracted imports into internal module edges and external packages."""
    resolver = _DependencyResolver(Path(root_path), extractions)
    return resolver.resolve_all()
