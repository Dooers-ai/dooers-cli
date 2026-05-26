"""Global CLI configuration: core URL, push URL, env.

Precedence: explicit CLI flag > env var > built-in default.
The top-level Typer callback resolves this once and stashes it on the
Typer context so every subcommand sees the same values.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_url: str
    push_url: str
    env: str

    @classmethod
    def resolve(
        cls,
        core_url: str | None = None,
        push_url: str | None = None,
        env: str | None = None,
    ) -> "Settings":
        return cls(
            core_url=(core_url or os.environ.get("DOOERS_CORE_URL") or "https://api.dooers.ai").rstrip("/"),
            push_url=(push_url or os.environ.get("DOOERS_PUSH_URL") or "https://push.dooers.ai").rstrip("/"),
            env=(env or os.environ.get("DOOERS_ENV") or "prod"),
        )
