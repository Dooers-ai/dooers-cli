"""dooers.yaml reader/writer."""

from pathlib import Path

import yaml
from dooers.protocol.agents import AgentManifest

MANIFEST_FILENAME = "dooers.yaml"


def read_manifest(directory: Path | None = None) -> AgentManifest | None:
    """Read and validate dooers.yaml from `directory` (default: cwd).

    Returns None if missing. Raises pydantic.ValidationError on schema violations.
    """
    target = (directory or Path.cwd()) / MANIFEST_FILENAME
    if not target.exists():
        return None
    raw = yaml.safe_load(target.read_text()) or {}
    return AgentManifest.model_validate(raw)


def write_manifest(manifest: AgentManifest, directory: Path | None = None) -> Path:
    """Write `manifest` to `directory/dooers.yaml`. Returns the path written."""
    target = (directory or Path.cwd()) / MANIFEST_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False))
    return target
