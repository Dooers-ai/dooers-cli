"""Session verification — forwards the Bearer token to core v2 /identity/me."""

import httpx
from fastapi import HTTPException, Request

from dooers_protocol.auth import AuthSession
from dooers_push.settings import Settings


async def verify_session(request: Request, settings: Settings) -> AuthSession:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth_header[len("Bearer "):]

    url = f"{settings.core_api_url}/api/v2/identity/me"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=settings.request_timeout,
            )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"core unreachable: {e}") from e

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="invalid session")

    data = resp.json().get("data", {})
    user_id = data.get("id") or data.get("user_id") or ""
    email = data.get("email", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="core returned no user id")
    return AuthSession(user_id=user_id, email=email)
