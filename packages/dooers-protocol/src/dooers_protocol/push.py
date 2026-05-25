"""Push request/response shapes and build status enum."""

from enum import Enum

from pydantic import BaseModel


class BuildStatus(str, Enum):
    queued = "queued"
    building = "building"
    deploying = "deploying"
    succeeded = "succeeded"
    failed = "failed"


class PushRequest(BaseModel):
    """Query/path inputs for `POST /v1/push/{agent_id}`.

    The archive itself is sent as multipart and not modeled here.
    """

    agent_id: str
    tag: str = "latest"
    env: str = "prod"


class PushResponse(BaseModel):
    """Returned to the CLI after a synchronous push completes (or fails)."""

    agent_id: str
    build_id: str
    image: str
    status: BuildStatus
    url: str | None = None
    error: str | None = None
