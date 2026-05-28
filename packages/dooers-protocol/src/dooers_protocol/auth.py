"""Auth-related wire shapes (session, whoami)."""

from pydantic import BaseModel


class AuthSession(BaseModel):
    """A verified session returned by core's /session/verify."""

    user_id: str
    email: str


class WhoamiResponse(BaseModel):
    """Response shape for `dooers whoami`."""

    user_id: str
    email: str
