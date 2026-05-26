"""Tests for .dooersignore parsing + archive creation."""

import tarfile
from pathlib import Path

from dooers.ignore import (
    DEFAULT_IGNORE_PATTERNS,
    is_ignored,
    load_ignore_patterns,
    make_archive,
)


def test_default_patterns_match_node_modules() -> None:
    assert is_ignored("node_modules/foo", DEFAULT_IGNORE_PATTERNS) is True
    assert is_ignored("src/main.py", DEFAULT_IGNORE_PATTERNS) is False


def test_directory_pattern_matches_subpaths() -> None:
    assert is_ignored(".git/HEAD", DEFAULT_IGNORE_PATTERNS) is True
    assert is_ignored(".gitignore", DEFAULT_IGNORE_PATTERNS) is True


def test_glob_pattern_matches() -> None:
    assert is_ignored("app.log", DEFAULT_IGNORE_PATTERNS) is True
    assert is_ignored("dist/main.js", DEFAULT_IGNORE_PATTERNS) is True


def test_load_merges_default_with_dooersignore(tmp_path: Path) -> None:
    (tmp_path / ".dooersignore").write_text("*.secret\nlocal/\n# a comment\n\n")
    patterns = load_ignore_patterns(tmp_path)
    assert "*.secret" in patterns
    assert "local/" in patterns
    # defaults still present
    assert "node_modules/" in patterns
    # comment & blank not added
    assert "# a comment" not in patterns


def test_make_archive_excludes_ignored(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    (tmp_path / "app.log").write_text("noise")

    archive_path = make_archive(directory=str(tmp_path))
    try:
        with tarfile.open(archive_path) as tar:
            names = sorted(tar.getnames())
    finally:
        Path(archive_path).unlink(missing_ok=True)

    assert "src/main.py" in names
    assert not any("node_modules" in n for n in names)
    assert "app.log" not in names
