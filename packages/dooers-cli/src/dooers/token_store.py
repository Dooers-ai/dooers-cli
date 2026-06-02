"""Persisted auth token at ~/.dooers/token.json (0600). Stores token + expiry."""

import json
import time
from pathlib import Path

DEFAULT_TOKEN_PATH = Path.home() / ".dooers" / "token.json"


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_TOKEN_PATH

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def load(self) -> str | None:
        return self._read().get("token") or None

    def expires_at(self) -> int:
        return int(self._read().get("expires_at", 0))

    def save(self, token: str, expires_at: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"token": token, "expires_at": expires_at}))
        self.path.chmod(0o600)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def is_token_expired(token: str | None, store: "TokenStore | None" = None) -> bool:
    """True if no token or the stored expiry has passed."""
    if not token:
        return True
    store = store or TokenStore()
    exp = store.expires_at()
    return exp == 0 or time.time() >= exp
