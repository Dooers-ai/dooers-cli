"""Push request/response shapes and build status enum."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel

from dooers.protocol.audit import AuditReport


class BuildStatus(str, Enum):
    queued = "queued"
    building = "building"
    deploying = "deploying"
    succeeded = "succeeded"
    failed = "failed"


def is_terminal(status: BuildStatus) -> bool:
    """True when a build has reached a final state (succeeded or failed)."""
    return status in (BuildStatus.succeeded, BuildStatus.failed)


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


class PushAcceptedResponse(BaseModel):
    """Returned by `POST /v1/push` (202): the build was triggered, poll for status."""

    build_id: str
    agent_id: str
    status: BuildStatus = BuildStatus.building
    audit: AuditReport | None = None


class BuildStatusResponse(BaseModel):
    """Returned by `GET /v1/builds/{build_id}`: the current state of a build.

    `error`, `failed_step`, and `error_class` are sanitized, user-facing fields
    populated only on failure. No infra detail (project ids, hosts, gs:// URIs,
    console URLs) ever reaches these fields — redaction happens server-side.
    """

    build_id: str
    agent_id: str
    status: BuildStatus
    phase: str | None = None
    url: str | None = None
    error: str | None = None
    failed_step: str | None = None
    error_class: Literal["user", "infra"] | None = None
    audit: AuditReport | None = None
    correlation_id: str | None = None


def format_push_failure(response: PushResponse | BuildStatusResponse) -> str:
    """Format a failed push response for terminal display (`dooers push`)."""
    lines = ["Deployment failed"]
    if response.failed_step:
        lines.append(f"Failed during: {response.failed_step}")
    if response.error:
        lines.append(response.error)
    # `build_log_url` (a console URL that embeds the project id) only exists on
    # the legacy PushResponse and is never present on BuildStatusResponse, which
    # carries a sanitized correlation_id for support instead.
    build_log_url = getattr(response, "build_log_url", None)
    correlation_id = getattr(response, "correlation_id", None)
    if build_log_url:
        lines.append(f"Build logs: {build_log_url}")
    elif correlation_id:
        lines.append(f"Reference: {correlation_id}")
    elif response.build_id:
        lines.append(f"Build ID: {response.build_id}")
    return "\n".join(lines)
