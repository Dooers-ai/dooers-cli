"""HTTP client for dooers-push (multipart upload + synchronous wait)."""

from pathlib import Path

import httpx

from dooers_protocol.errors import ErrorEnvelope
from dooers_protocol.push import PushResponse


class PushClientError(RuntimeError):
    def __init__(self, message: str, envelope: ErrorEnvelope | None = None) -> None:
        super().__init__(message)
        self.envelope = envelope


class PushClient:
    def __init__(self, base_url: str, token: str, timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout

    def push(
        self,
        agent_id: str,
        archive_path: Path,
        tag: str = "latest",
        env: str = "prod",
    ) -> PushResponse:
        url = f"{self.base_url}/v1/push/{agent_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        params = {"tag": tag, "env": env}
        with archive_path.open("rb") as f:
            files = {"archive": (archive_path.name, f, "application/gzip")}
            try:
                r = httpx.post(
                    url,
                    headers=headers,
                    params=params,
                    files=files,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as e:
                raise PushClientError(f"push request failed: {e}") from e

        if r.status_code >= 400:
            try:
                envelope = ErrorEnvelope.model_validate(r.json())
                raise PushClientError(envelope.message, envelope=envelope)
            except (ValueError, TypeError):
                raise PushClientError(f"push failed (HTTP {r.status_code}): {r.text}")

        return PushResponse.model_validate(r.json())
