"""Session verification — forwards bearer token to core's /session/verify."""

import httpx
from fastapi import HTTPException, Request

from dooers_protocol.auth import AuthSession
from dooers_push.settings import Settings


async def verify_session(request: Request, settings: Settings) -> AuthSession:
    """Verify the incoming Bearer token by forwarding it to core."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth_header[len("Bearer "):]

    verify_url = f"{settings.core_api_url}/api/v1/session/verify"
    try:
        async with httpx.AsyncClient() as client:
            # Try Bearer first, fall back to cookie (v1 server uses this same dance).
            resp = await client.get(
                verify_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=settings.request_timeout,
            )
            if resp.status_code != 200:
                resp = await client.get(
                    verify_url,
                    cookies={"auth": token},
                    timeout=settings.request_timeout,
                )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"core unreachable: {e}") from e

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="invalid session")

    body = resp.json()
    output = body.get("output", body) if isinstance(body, dict) else body
    user_id = output.get("user_id") or output.get("id") or output.get("user", {}).get("id", "")
    email = output.get("email") or output.get("user", {}).get("email", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="core returned no user_id")
    return AuthSession(user_id=user_id, email=email)
