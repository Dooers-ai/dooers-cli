"""HTTP client for the Dooers core API (auth, agent records).

Reference behavior: see v1 CLI flow in ../../../deploy-service/cli/dooers/cli.py
- /api/v1/session/request  → returns {"output": {"email_id": "..."}}
- /api/v1/session/create   → returns auth token via `auth` cookie
- /api/v1/session/verify   → returns user dict
- /api/v1/session/remove   → logout
"""

import httpx
from pydantic import ValidationError

from dooers_protocol.agents import AgentRecord, CreateAgentRequest
from dooers_protocol.auth import WhoamiResponse


class CoreClientError(RuntimeError):
    """Anything we'd want to surface as a CLI-friendly error."""


class CoreClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout

    # ------ auth ---------------------------------------------------------

    def login_request_otp(self, email: str) -> str:
        """POST /api/v1/session/request. Returns `email_id`."""
        try:
            r = httpx.post(
                f"{self.base_url}/api/v1/session/request",
                json={"email": email, "method": "email"},
                timeout=self._timeout,
            )
            r.raise_for_status()
            data = r.json()
            email_id = data.get("output", {}).get("email_id")
            if not email_id:
                raise CoreClientError(f"core returned no email_id (body: {data})")
            return email_id
        except httpx.HTTPError as e:
            raise CoreClientError(f"failed to request OTP: {e}") from e

    def login_verify_otp(self, email_id: str, code: str) -> str:
        """POST /api/v1/session/create. Returns the auth token (cookie value)."""
        try:
            r = httpx.post(
                f"{self.base_url}/api/v1/session/create",
                json={"email_id": email_id, "code": code},
                timeout=self._timeout,
            )
            r.raise_for_status()
            cookie = r.cookies.get("auth")
            if cookie:
                return cookie
            # fallback: token may also appear in body
            token = r.json().get("output", {}).get("token")
            if token:
                return token
            raise CoreClientError("core returned no auth token")
        except httpx.HTTPError as e:
            raise CoreClientError(f"failed to verify OTP: {e}") from e

    def whoami(self) -> WhoamiResponse:
        if not self.token:
            raise CoreClientError("not authenticated")
        try:
            r = httpx.get(
                f"{self.base_url}/api/v1/session/verify",
                cookies={"auth": self.token},
                timeout=self._timeout,
            )
            r.raise_for_status()
            data = r.json()
            output = data.get("output", data)
            # The core response shape isn't strict; accept either flat or nested.
            user_id = output.get("user_id") or output.get("id") or output.get("user", {}).get("id", "")
            email = output.get("email") or output.get("user", {}).get("email", "")
            try:
                return WhoamiResponse(user_id=user_id, email=email)
            except ValidationError as e:
                raise CoreClientError(f"unexpected /session/verify shape: {data}") from e
        except httpx.HTTPError as e:
            raise CoreClientError(f"whoami failed: {e}") from e

    def logout(self) -> None:
        if not self.token:
            return
        try:
            httpx.post(
                f"{self.base_url}/api/v1/session/remove",
                cookies={"auth": self.token},
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            pass  # logout is best-effort

    # ------ agents (stub for M2) -----------------------------------------

    def list_agents(self) -> list[AgentRecord]:
        raise NotImplementedError("M2")

    def create_agent(self, req: CreateAgentRequest) -> AgentRecord:
        raise NotImplementedError("M2")

    def get_agent(self, agent_id: str) -> AgentRecord:
        raise NotImplementedError("M2")
