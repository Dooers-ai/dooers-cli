"""HTTP client for dooers-push (async: 202 trigger + build-status polling)."""

from pathlib import Path

import httpx
from dooers.protocol.errors import ErrorEnvelope
from dooers.protocol.push import BuildStatusResponse, PushAcceptedResponse

# 5xx responses that mean "try again later" rather than "this build is broken".
_TRANSIENT_STATUS = frozenset({502, 503, 504})


class PushClientError(RuntimeError):
    def __init__(self, message: str, envelope: ErrorEnvelope | None = None) -> None:
        super().__init__(message)
        self.envelope = envelope


class PushTransientError(PushClientError):
    """A transient failure (5xx / network) the caller may safely retry."""


class PushClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def push(
        self,
        agent_id: str,
        archive_path: Path,
        tag: str = "latest",
        env: str = "prod",
    ) -> PushAcceptedResponse:
        """Trigger a build. Returns the 202 acceptance; poll get_build_status next."""
        url = f"{self.base_url}/v1/push/{agent_id}"
        params = {"tag": tag, "env": env}
        with archive_path.open("rb") as f:
            files = {"archive": (archive_path.name, f, "application/gzip")}
            try:
                r = httpx.post(
                    url,
                    headers=self._headers,
                    params=params,
                    files=files,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as e:
                raise PushTransientError(f"push request failed: {e}") from e

        self._raise_for_status(r)
        return PushAcceptedResponse.model_validate(r.json())

    def get_build_status(self, build_id: str) -> BuildStatusResponse:
        """Read the current build status. Raises PushTransientError on 5xx/network
        so a polling caller can retry; raises PushClientError on a 4xx envelope."""
        url = f"{self.base_url}/v1/builds/{build_id}"
        try:
            r = httpx.get(url, headers=self._headers, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise PushTransientError(f"build status request failed: {e}") from e

        self._raise_for_status(r)
        return BuildStatusResponse.model_validate(r.json())

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code < 400:
            return
        if r.status_code in _TRANSIENT_STATUS:
            raise PushTransientError(f"transient upstream error (HTTP {r.status_code})")
        try:
            envelope = ErrorEnvelope.model_validate(r.json())
        except (ValueError, TypeError):
            raise PushClientError(f"push failed (HTTP {r.status_code}): {r.text}") from None
        raise PushClientError(envelope.message, envelope=envelope)
