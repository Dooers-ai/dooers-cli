"""`.dooersignore` parsing + cwd archiving with default ignore patterns.

Ported from v1 cli.py — refactored into pure functions for testability.
POC scaffold — full port lands in the next milestone.
"""

DEFAULT_IGNORE_PATTERNS: list[str] = [
    ".git/",
    ".gitignore",
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


def load_ignore_patterns(directory: str = ".") -> list[str]:
    """Return default patterns merged with any from `.dooersignore`."""
    raise NotImplementedError("scaffold")


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Check whether `rel_path` matches any pattern (gitignore-style)."""
    raise NotImplementedError("scaffold")


def make_archive(directory: str = ".") -> str:
    """Create a temp .tar.gz of `directory` respecting ignore patterns.

    Returns the absolute path to the temp archive (caller is responsible
    for cleanup).
    """
    raise NotImplementedError("scaffold")
