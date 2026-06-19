"""`.dooersignore` parsing + cwd archiving with default ignore patterns.

Ported from v1 cli.py — refactored into pure functions for testability.
Reference: ../../../deploy-service/cli/dooers/cli.py
"""

import fnmatch
import os
import tarfile
import tempfile
from pathlib import Path

DEFAULT_IGNORE_PATTERNS: list[str] = [
    ".git/",
    ".gitignore",
    ".env",
    ".env.*",
    ".venv/",
    "venv/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    ".DS_Store",
    "dist/",
    "build/",
    "*.log",
]


def load_ignore_patterns(directory: Path | None = None) -> list[str]:
    """Return default patterns merged with any from `.dooersignore`."""
    directory = directory or Path.cwd()
    patterns = list(DEFAULT_IGNORE_PATTERNS)
    ignore_file = directory / ".dooersignore"
    if ignore_file.exists():
        for raw in ignore_file.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    return patterns


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Check whether `rel_path` matches any pattern (gitignore-style)."""
    posix_path = rel_path.replace(os.sep, "/")
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        if pat.endswith("/"):
            prefix = pat[:-1]
            if posix_path == prefix or posix_path.startswith(prefix + "/"):
                return True
        if pat.startswith("/"):
            if fnmatch.fnmatch(posix_path, pat.lstrip("/")):
                return True
        if fnmatch.fnmatch(posix_path, pat):
            return True
        if "/" not in pat and pat in posix_path.split("/"):
            return True
    return False


def make_archive(directory: str = ".") -> str:
    """Create a temp .tar.gz of `directory` respecting ignore patterns.

    Returns the absolute path to the temp archive (caller is responsible
    for cleanup).
    """
    patterns = load_ignore_patterns(Path(directory))
    tmpfd, tmppath = tempfile.mkstemp(suffix=".tar.gz", prefix="dooers-")
    os.close(tmpfd)
    with tarfile.open(tmppath, "w:gz") as tar:
        for root, dirs, files in os.walk(directory):
            relroot = os.path.relpath(root, directory)
            if relroot == ".":
                relroot = ""
            # prune ignored dirs in-place
            pruned = [
                d for d in dirs
                if is_ignored((os.path.join(relroot, d) if relroot else d) + "/", patterns)
            ]
            for d in pruned:
                dirs.remove(d)
            for name in files:
                rel = os.path.join(relroot, name) if relroot else name
                if is_ignored(rel, patterns):
                    continue
                tar.add(os.path.join(root, name), arcname=rel)
    return tmppath
