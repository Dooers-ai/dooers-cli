"""Helpers for maintaining env.prod alongside dooers push."""

from __future__ import annotations

import re
from pathlib import Path

_ENV_PROD = "env.prod"
_SEED_LINE = re.compile(r"^AGENT_SEED_SECRET=.*$", re.MULTILINE)


def upsert_agent_seed_secret(directory: Path, secret: str) -> Path:
    """Write or replace AGENT_SEED_SECRET in env.prod (creates file if missing)."""
    path = directory / _ENV_PROD
    line = f"AGENT_SEED_SECRET={secret.strip()}\n"
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if _SEED_LINE.search(text):
            text = _SEED_LINE.sub(line.rstrip("\n"), text)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += "\n# Runtime API key (core ↔ agent settings.seed)\n" + line
        path.write_text(text, encoding="utf-8")
    else:
        path.write_text(
            "# Production runtime env (uploaded by dooers push; never commit secrets to git)\n"
            + line,
            encoding="utf-8",
        )
    return path
