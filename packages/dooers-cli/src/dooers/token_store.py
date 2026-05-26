"""Persisted auth token at ~/.dooers/token with 0600 permissions.

Also exposes is_token_expired() — parses JWT `exp` claim without verifying
the signature (we re-verify against core on every authenticated request).
"""

import base64
import json
import time
from pathlib import Path

DEFAULT_TOKEN_PATH = Path.home() / ".dooers" / "token"


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_TOKEN_PATH

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            return self.path.read_text().strip() or None
        except OSError:
            return None

    def save(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(token)
        self.path.chmod(0o600)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def is_token_expired(token: str) -> bool:
    """Decode JWT payload and check `exp`. Returns True on any parse error."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return True
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = int(payload.get("exp", 0))
        return time.time() >= exp
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return True
