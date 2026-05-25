"""dooers.yaml reader/writer.

POC scaffold — actual implementation lands in the next milestone.
See docs/superpowers/specs/2026-05-26-dooers-cli-v2-design.md §7.
"""

from pathlib import Path

from dooers_protocol.agents import AgentManifest

MANIFEST_FILENAME = "dooers.yaml"


def read_manifest(directory: Path | None = None) -> AgentManifest | None:
    """Read and validate dooers.yaml from `directory` (default: cwd).

    Returns None if the file does not exist. Raises ValidationError on
    schema violations.
    """
    raise NotImplementedError("scaffold — implement in next milestone")


def write_manifest(manifest: AgentManifest, directory: Path | None = None) -> Path:
    """Write `manifest` to `directory/dooers.yaml` (default: cwd).

    Returns the path written.
    """
    raise NotImplementedError("scaffold — implement in next milestone")
