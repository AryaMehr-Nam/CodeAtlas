"""Unit tests for the gitignore-aware repository walker."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codeatlas.models.config import Language, ScanConfig
from codeatlas.scanner.walker import walk_repository


def _write_text(path: Path, content: str = "x = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_walker_nested_gitignore_is_strictly_scoped(tmp_path: Path) -> None:
    _write_text(tmp_path / ".gitignore", "*.tmp\n")
    _write_text(tmp_path / "keep.py")
    _write_text(tmp_path / "root.log", "root log")
    _write_text(tmp_path / "ignored.tmp", "tmp")

    # Nested .gitignore must affect ONLY files under `sub/`, never `other/`.
    _write_text(tmp_path / "sub" / ".gitignore", "*.log\n!keep.log\n")
    _write_text(tmp_path / "sub" / "drop.log", "drop")
    _write_text(tmp_path / "sub" / "keep.log", "keep")
    _write_text(tmp_path / "sub" / "module.py")
    _write_text(tmp_path / "other" / "stay.log", "stay")

    result = walk_repository(tmp_path)
    rels = [f.relative_path for f in result.files]

    assert "keep.py" in rels
    assert "root.log" in rels
    assert "other/stay.log" in rels
    assert "sub/keep.log" in rels
    assert "sub/module.py" in rels
    assert "ignored.tmp" not in rels
    assert "sub/drop.log" not in rels


def test_walker_default_ignores_and_test_filtering(tmp_path: Path) -> None:
    _write_text(tmp_path / "src" / "app.py")
    _write_text(tmp_path / "src" / "bundle.min.js", "console.log(1);")
    _write_text(tmp_path / "src" / "vendor.bundle.js", "console.log(1);")
    _write_text(tmp_path / "node_modules" / "pkg" / "index.js", "module.exports = {};")
    _write_text(tmp_path / ".venv" / "lib.py")
    _write_text(tmp_path / "__pycache__" / "app.cpython-311.pyc")
    _write_text(tmp_path / "tests" / "test_app.py")
    _write_text(tmp_path / "src" / "widget.spec.ts", "export {};")

    with_tests = walk_repository(tmp_path, ScanConfig(include_tests=True))
    rels_with = [f.relative_path for f in with_tests.files]
    assert rels_with == ["src/app.py", "src/widget.spec.ts", "tests/test_app.py"]

    without_tests = walk_repository(tmp_path, ScanConfig(include_tests=False))
    rels_without = [f.relative_path for f in without_tests.files]
    assert rels_without == ["src/app.py"]


def test_walker_exclude_globs_binary_oversized_and_max_files(tmp_path: Path) -> None:
    _write_text(tmp_path / "a.py")
    _write_text(tmp_path / "b.py")
    _write_text(tmp_path / "c.py")
    _write_text(tmp_path / "notes.md", "# readme\n")
    _write_text(tmp_path / "skip_me.py")
    _write_text(tmp_path / "huge.py", "x = 1\n" * 50)
    (tmp_path / "binary.py").write_bytes(b"# fake\x00\x00\x01binary")

    cfg = ScanConfig(
        max_files=2,
        max_file_bytes=64,
        exclude_globs=["skip_me.py"],
    )
    result = walk_repository(tmp_path, cfg, supported_only=True)

    assert result.skipped_binary == 1
    assert result.skipped_oversized == 1
    assert result.total_files_seen == 3
    assert result.truncated is True
    assert [f.relative_path for f in result.files] == ["a.py", "b.py"]
    assert all(f.language == Language.PYTHON for f in result.files)
    assert all("\\" not in f.relative_path for f in result.files)


def test_walker_handles_symlinks_and_cycles_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codeatlas.scanner import walker as walker_mod

    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    _write_text(repo / "inside.py")
    _write_text(repo / "broken.py")
    _write_text(repo / "escape.py")
    _write_text(repo / "loop_dir" / "nested.py")
    _write_text(outside / "secret.py")

    orig_stat = os.stat
    orig_realpath = os.path.realpath
    orig_is_safe = walker_mod._is_safe_entry_target

    def fake_is_safe(entry_path: str, root_real: str, *, check_symlink: bool) -> bool:
        name = Path(entry_path).name
        if name in {"broken.py", "escape.py"}:
            return orig_is_safe(entry_path, root_real, check_symlink=True)
        return orig_is_safe(entry_path, root_real, check_symlink=check_symlink)

    def fake_stat(path: str | os.PathLike[str], *args: object, **kwargs: object) -> os.stat_result:
        name = Path(path).name
        if name == "broken.py":
            raise OSError("broken symlink target")
        if Path(path) == repo or name == "loop_dir":
            st = orig_stat(repo)
            # Force st_ino == 0 to verify Windows realpath cycle deduplication too.
            return os.stat_result((st.st_mode, 0, st.st_dev, *st[3:]))
        return orig_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    def fake_realpath(path: str | os.PathLike[str]) -> str:
        name = Path(path).name
        if name == "escape.py":
            return orig_realpath(outside / "secret.py")
        if name == "loop_dir":
            return orig_realpath(repo)
        return orig_realpath(path)

    monkeypatch.setattr(walker_mod, "_is_safe_entry_target", fake_is_safe)
    monkeypatch.setattr(walker_mod.os, "stat", fake_stat)
    monkeypatch.setattr(walker_mod.os.path, "realpath", fake_realpath)

    result = walk_repository(repo, supported_only=True)
    assert [f.relative_path for f in result.files] == ["inside.py"]
    assert result.skipped_symlinks == 3


def test_walker_invalid_root_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError):
        walk_repository(missing)

    file_root = tmp_path / "file.txt"
    file_root.write_text("hi", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        walk_repository(file_root)
