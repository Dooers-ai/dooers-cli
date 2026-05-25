"""HTTP client for dooers-push (multipart upload + synchronous wait).

POC scaffold — actual implementation lands in the next milestone.
"""

from pathlib import Path

from dooers_protocol.push import PushResponse


class PushClient:
    """Thin wrapper around the dooers-push API."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def push(
        self,
        agent_id: str,
        archive_path: Path,
        tag: str = "latest",
        env: str = "prod",
    ) -> PushResponse:
        """POST /v1/push/{agent_id} with the archive as multipart.

        Synchronous: blocks up to ~600s while dooers-push polls Cloud Build.
        """
        raise NotImplementedError("scaffold")
