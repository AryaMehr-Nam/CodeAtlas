"""Gitignore-aware repository file walker with Windows and symlink safety."""

from __future__ import annotations

import fnmatch
import os
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import PathSpec
from pathspec.pattern import Pattern

from codeatlas.models.config import Language, ScanConfig
from codeatlas.models.graph import ParseWarning
from codeatlas.scanner.parser_registry import detect_language

DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "vendor",
        ".next",
        ".mypy_cache",
        ".pytest_cache",
    }
)

DEFAULT_IGNORED_FILE_GLOBS: tuple[str, ...] = ("*.min.js", "*.bundle.js")

_DEFAULT_TEST_DIRS: frozenset[str] = frozenset({"tests", "test", "__tests__"})
_DEFAULT_TEST_FILE_GLOBS: tuple[str, ...] = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*.test.js",
    "*.spec.js",
    "*.test.jsx",
    "*.spec.jsx",
    "*.test.ts",
    "*.spec.ts",
    "*.test.tsx",
    "*.spec.tsx",
    "*.test.mjs",
    "*.spec.mjs",
    "*_test.go",
    "*Test.php",
    "*TestCase.php",
)

_BINARY_SNIFF_BYTES = 8192


@dataclass(frozen=True, slots=True)
class WalkedFile:
    """A source or text file discovered by the repository walker."""

    path: Path
    relative_path: str
    size_bytes: int
    language: Language | None


@dataclass(frozen=True, slots=True)
class WalkResult:
    """Deterministic summary of repository walking."""

    root_path: Path
    files: list[WalkedFile]
    total_files_seen: int
    truncated: bool
    warnings: list[ParseWarning] = field(default_factory=list)
    skipped_binary: int = 0
    skipped_oversized: int = 0
    skipped_symlinks: int = 0


@dataclass(frozen=True, slots=True)
class _ScopedIgnore:
    """A ``.gitignore`` matcher scoped strictly to its containing directory."""

    base_rel: str
    spec: PathSpec[Pattern]


@dataclass(slots=True)
class _WalkAccumulator:
    """Mutable counters and collected entries during repository traversal."""

    files: list[WalkedFile] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)
    skipped_binary: int = 0
    skipped_oversized: int = 0
    skipped_symlinks: int = 0


def _is_within_root(target_real: str, root_real: str) -> bool:
    """Return True when ``target_real`` resides at or below ``root_real``."""
    try:
        return os.path.commonpath([root_real, target_real]) == root_real
    except ValueError:
        return False


def _dir_identity(dir_path: Path, st: os.stat_result) -> tuple[int, int] | str:
    """Compute a cross-platform directory identity key.

    Uses ``(st_dev, st_ino)`` when ``st_ino != 0`` and falls back to normalized
    ``os.path.realpath()`` on filesystems (e.g. some Windows volumes) where
    inode numbers are zero.
    """
    if st.st_ino != 0:
        return (st.st_dev, st.st_ino)
    return os.path.normcase(os.path.realpath(dir_path))


def _is_binary_file(file_path: Path) -> bool:
    """Return True if the first 8KB of ``file_path`` contains a NUL byte."""
    with file_path.open("rb") as handle:
        chunk = handle.read(_BINARY_SNIFF_BYTES)
    return b"\x00" in chunk


def _matches_any_glob(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _is_safe_entry_target(entry_path: str, root_real: str, *, check_symlink: bool) -> bool:
    """Verify an entry is neither broken/cyclic nor an escaping symlink/junction."""
    is_junction = hasattr(os.path, "isjunction") and os.path.isjunction(entry_path)
    if check_symlink or is_junction:
        try:
            os.stat(entry_path)
        except OSError:
            return False
        target_real = os.path.normcase(os.path.realpath(entry_path))
        return _is_within_root(target_real, root_real)
    return True


def _load_scoped_gitignore(
    dir_path: Path,
    dir_rel: str,
    root_real: str,
    warnings: list[ParseWarning],
) -> _ScopedIgnore | None:
    """Load and compile ``dir_path/.gitignore`` scoped strictly to ``dir_rel``."""
    gitignore_path = dir_path / ".gitignore"
    try:
        if not gitignore_path.exists():
            return None
        if not _is_safe_entry_target(
            str(gitignore_path),
            root_real,
            check_symlink=gitignore_path.is_symlink(),
        ):
            return None
        if not gitignore_path.is_file():
            return None
        lines = gitignore_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as exc:
        rel_warn = posixpath.join(dir_rel, ".gitignore") if dir_rel else ".gitignore"
        warnings.append(
            ParseWarning(
                path=rel_warn,
                message=f"Unable to read .gitignore: {exc}",
            )
        )
        return None

    spec = PathSpec.from_lines("gitwildmatch", lines)
    return _ScopedIgnore(base_rel=dir_rel, spec=spec)


def _is_gitignored(
    entry_rel: str,
    is_dir: bool,
    active_ignores: tuple[_ScopedIgnore, ...],
) -> bool:
    """Evaluate stacked directory-scoped ``.gitignore`` matchers from outer to inner."""
    ignored = False
    for scoped in active_ignores:
        if scoped.base_rel == "":
            rel_to_scope = entry_rel
        else:
            prefix = f"{scoped.base_rel}/"
            if not entry_rel.startswith(prefix):
                continue
            rel_to_scope = entry_rel[len(prefix) :]
        candidate = f"{rel_to_scope}/" if is_dir else rel_to_scope
        check = scoped.spec.check_file(candidate)
        if check.include is not None:
            ignored = check.include
    return ignored


def _walk_dir(
    current_dir: Path,
    rel_dir: str,
    root_real: str,
    config: ScanConfig,
    exclude_spec: PathSpec[Pattern] | None,
    inherited_ignores: tuple[_ScopedIgnore, ...],
    visited_dirs: set[tuple[int, int] | str],
    acc: _WalkAccumulator,
    *,
    supported_only: bool,
) -> None:
    local_ignore = _load_scoped_gitignore(current_dir, rel_dir, root_real, acc.warnings)
    active_ignores = (*inherited_ignores, local_ignore) if local_ignore else inherited_ignores

    try:
        with os.scandir(current_dir) as it:
            entries = sorted(it, key=lambda entry: entry.name)
    except OSError as exc:
        acc.warnings.append(
            ParseWarning(
                path=rel_dir or ".",
                message=f"Unable to scan directory: {exc}",
            )
        )
        return

    for entry in entries:
        name = entry.name
        entry_rel = posixpath.join(rel_dir, name) if rel_dir else name

        try:
            is_sym = entry.is_symlink()
        except OSError:
            acc.skipped_symlinks += 1
            continue

        if not _is_safe_entry_target(entry.path, root_real, check_symlink=is_sym):
            acc.skipped_symlinks += 1
            continue

        try:
            is_dir = entry.is_dir(follow_symlinks=True)
        except OSError:
            acc.skipped_symlinks += 1
            continue

        if is_dir:
            if name in DEFAULT_IGNORED_DIRS:
                continue
            if not config.include_tests and name in _DEFAULT_TEST_DIRS:
                continue
            if exclude_spec is not None and exclude_spec.match_file(f"{entry_rel}/"):
                continue
            if _is_gitignored(entry_rel, is_dir=True, active_ignores=active_ignores):
                continue

            entry_path = Path(entry.path)
            target_real = os.path.normcase(os.path.realpath(entry_path))
            if not _is_within_root(target_real, root_real):
                acc.skipped_symlinks += 1
                continue

            try:
                dir_stat = os.stat(entry_path)
            except OSError:
                acc.skipped_symlinks += 1
                continue

            identity = _dir_identity(entry_path, dir_stat)
            if identity in visited_dirs:
                acc.skipped_symlinks += 1
                continue
            visited_dirs.add(identity)

            _walk_dir(
                entry_path,
                entry_rel,
                root_real,
                config,
                exclude_spec,
                active_ignores,
                visited_dirs,
                acc,
                supported_only=supported_only,
            )
            continue

        try:
            is_file = entry.is_file(follow_symlinks=True)
        except OSError:
            acc.skipped_symlinks += 1
            continue
        if not is_file:
            continue

        if name == ".git" or _matches_any_glob(name, DEFAULT_IGNORED_FILE_GLOBS):
            continue
        if not config.include_tests and _matches_any_glob(name, _DEFAULT_TEST_FILE_GLOBS):
            continue
        if exclude_spec is not None and exclude_spec.match_file(entry_rel):
            continue
        if _is_gitignored(entry_rel, is_dir=False, active_ignores=active_ignores):
            continue

        language = detect_language(entry_rel)
        if supported_only and language is None:
            continue

        try:
            file_stat = entry.stat(follow_symlinks=True)
        except OSError as exc:
            acc.warnings.append(ParseWarning(path=entry_rel, message=f"Unable to stat file: {exc}"))
            continue

        if file_stat.st_size > config.max_file_bytes:
            acc.skipped_oversized += 1
            continue

        entry_path = Path(entry.path)
        try:
            if _is_binary_file(entry_path):
                acc.skipped_binary += 1
                continue
        except OSError as exc:
            acc.warnings.append(ParseWarning(path=entry_rel, message=f"Unable to read file: {exc}"))
            continue

        acc.files.append(
            WalkedFile(
                path=entry_path,
                relative_path=entry_rel,
                size_bytes=file_stat.st_size,
                language=language,
            )
        )


def walk_repository(
    root: str | Path,
    config: ScanConfig | None = None,
    *,
    supported_only: bool = False,
) -> WalkResult:
    """Walk ``root`` respecting nested ``.gitignore`` scopes and safety limits.

    Args:
        root: Repository root directory to walk.
        config: Optional scan configuration; defaults to ``ScanConfig()``.
        supported_only: When True, include only files matching supported languages
            before enforcing ``config.max_files``.

    Returns:
        A deterministic ``WalkResult`` sorted by POSIX relative path.
    """
    cfg = config or ScanConfig()
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Repository path does not exist: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"Repository path is not a directory: {root_path}")

    root_real = os.path.normcase(os.path.realpath(root_path))
    root_stat = os.stat(root_path)
    visited_dirs: set[tuple[int, int] | str] = {_dir_identity(root_path, root_stat)}

    exclude_spec = (
        PathSpec.from_lines("gitwildmatch", cfg.exclude_globs) if cfg.exclude_globs else None
    )

    acc = _WalkAccumulator()
    _walk_dir(
        current_dir=root_path,
        rel_dir="",
        root_real=root_real,
        config=cfg,
        exclude_spec=exclude_spec,
        inherited_ignores=(),
        visited_dirs=visited_dirs,
        acc=acc,
        supported_only=supported_only,
    )

    acc.files.sort(key=lambda item: item.relative_path)
    total_files_seen = len(acc.files)
    truncated = total_files_seen > cfg.max_files
    capped_files = acc.files[: cfg.max_files]

    return WalkResult(
        root_path=root_path,
        files=capped_files,
        total_files_seen=total_files_seen,
        truncated=truncated,
        warnings=acc.warnings,
        skipped_binary=acc.skipped_binary,
        skipped_oversized=acc.skipped_oversized,
        skipped_symlinks=acc.skipped_symlinks,
    )
