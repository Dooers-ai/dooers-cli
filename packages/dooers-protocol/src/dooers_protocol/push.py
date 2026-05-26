"""Push request/response shapes and build status enum."""

from enum import Enum

from pydantic import BaseModel

from dooers_protocol.audit import AuditReport


class BuildStatus(str, Enum):
    queued = "queued"
    building = "building"
    deploying = "deploying"
    succeeded = "succeeded"
    failed = "failed"


class PushRequest(BaseModel):
    agent_id: str
    tag: str = "latest"
    env: str = "prod"


class PushResponse(BaseModel):
    agent_id: str
    build_id: str
    image: str
    status: BuildStatus
    url: str | None = None
    error: str | None = None
    audit: AuditReport | None = None
