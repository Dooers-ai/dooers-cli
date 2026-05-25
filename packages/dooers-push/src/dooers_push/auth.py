"""Session verification — forwards bearer token to core's /session/verify.

POC scaffold.
"""

from fastapi import HTTPException, Request

from dooers_protocol.auth import AuthSession


async def verify_session(request: Request) -> AuthSession:
    """Extract bearer token from Authorization header, verify with core.

    Returns the verified session. Raises HTTPException(401) if invalid.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    raise NotImplementedError("scaffold — call core /session/verify in next milestone")
