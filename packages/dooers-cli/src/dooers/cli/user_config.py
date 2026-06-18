"""~/.dooers/config.json — non-secret CLI prefs (default org)."""

import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".dooers" / "config.json"


class UserConfig:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_CONFIG_PATH

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def get_default_org(self) -> str | None:
        return self._read().get("default_org")

    def set_default_org(self, org_id: str) -> None:
        data = self._read()
        data["default_org"] = org_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))
