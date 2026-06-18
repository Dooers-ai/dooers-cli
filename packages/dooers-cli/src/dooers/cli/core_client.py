"""HTTP client for Dooers core v2 (better-auth OTP + agents)."""

import time

import httpx
from dooers.protocol.auth import WhoamiResponse

ACCESS_TOKEN_FALLBACK_TTL = 60 * 60 * 24 * 7  # 7d if core doesn't tell us


class CoreClientError(RuntimeError):
    """CLI-friendly error."""


def _data(resp: httpx.Response) -> dict:
    body = resp.json()
    if isinstance(body, dict) and body.get("success") is False:
        raise CoreClientError(body.get("error", {}).get("message", f"HTTP {resp.status_code}"))
    if resp.status_code >= 400:
        raise CoreClientError(f"HTTP {resp.status_code}")
    return body.get("data", body) if isinstance(body, dict) else body


class CoreClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 15.0) -> None:
        self.api = base_url.rstrip("/") + "/api/v2"
        self.token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # ---- auth ----
    def auth_method(self) -> str:
        try:
            r = httpx.get(f"{self.api}/identity/auth-method", timeout=self._timeout)
            return _data(r).get("method", "otp")
        except httpx.HTTPError as e:
            raise CoreClientError(f"auth-method failed: {e}") from e

    def send_otp(self, email: str) -> None:
        try:
            r = httpx.post(
                f"{self.api}/auth/email-otp/send-verification-otp",
                json={"email": email, "type": "sign-in"},
                timeout=self._timeout,
            )
            _data(r)
        except httpx.HTTPError as e:
            raise CoreClientError(f"failed to send code: {e}") from e

    def verify_otp(self, email: str, code: str) -> tuple[str, int]:
        """Returns (bearer_token, expires_at_epoch)."""
        try:
            r = httpx.post(
                f"{self.api}/auth/sign-in/email-otp",
                json={"email": email, "otp": code},
                timeout=self._timeout,
            )
            _data(r)  # raises on error envelope
            token = r.headers.get("set-auth-token")
            if not token:
                # fallback: mint via /identity/token using the session cookie just set
                tr = httpx.post(
                    f"{self.api}/identity/token", cookies=r.cookies, timeout=self._timeout
                )
                d = _data(tr)
                token = d.get("accessToken")
                if not token:
                    raise CoreClientError("core returned no access token")
                return token, int(time.time()) + int(d.get("expiresIn", ACCESS_TOKEN_FALLBACK_TTL))
            return token, int(time.time()) + ACCESS_TOKEN_FALLBACK_TTL
        except httpx.HTTPError as e:
            raise CoreClientError(f"failed to verify code: {e}") from e

    def me(self) -> WhoamiResponse:
        try:
            r = httpx.get(f"{self.api}/identity/me", headers=self._headers(), timeout=self._timeout)
            d = _data(r)
            # core v2 /identity/me → data.user.{id,email}; tolerate a flat shape too.
            user = d.get("user", d)
            return WhoamiResponse(
                user_id=user.get("id") or user.get("userId") or "",
                email=user.get("email", ""),
            )
        except httpx.HTTPError as e:
            raise CoreClientError(f"me failed: {e}") from e

    def revoke(self) -> None:
        try:
            httpx.post(
                f"{self.api}/identity/revoke", headers=self._headers(), timeout=self._timeout
            )
        except httpx.HTTPError:
            pass  # best-effort

    def list_organizations(self) -> list[dict]:
        try:
            r = httpx.get(
                f"{self.api}/organizations", headers=self._headers(), timeout=self._timeout
            )
            result = _data(r)
            return list(result) if isinstance(result, list) else []
        except httpx.HTTPError as e:
            raise CoreClientError(f"list organizations failed: {e}") from e
