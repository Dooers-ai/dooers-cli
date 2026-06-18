"""Push request/response shapes and build status enum."""

from enum import Enum

from pydantic import BaseModel

from dooers.protocol.audit import AuditReport


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
    # Populated on failed builds so creators can see where/why deploy stopped.
    failed_step: str | None = None
    build_log_url: str | None = None


def format_push_failure(response: PushResponse) -> str:
    """Format a failed push response for terminal display (`dooers push`)."""
    lines = ["Deployment failed"]
    if response.failed_step:
        lines.append(f"Failed during: {response.failed_step}")
    if response.error:
        lines.append(response.error)
    if response.build_log_url:
        lines.append(f"Build logs: {response.build_log_url}")
    elif response.build_id:
        lines.append(f"Build ID: {response.build_id}")
    return "\n".join(lines)
