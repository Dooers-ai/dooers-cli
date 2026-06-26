"""Push failure display tests."""

from dooers.protocol.errors import ErrorCode
from dooers.protocol.push import BuildStatus, PushResponse, format_push_failure

from dooers.cli.push_client import friendly_push_error


def test_friendly_push_error_org_not_provisioned() -> None:
    msg = friendly_push_error(ErrorCode.org_not_provisioned, "raw server message")
    assert "set up for agent hosting" in msg.lower()
    assert "raw server message" not in msg


def test_friendly_push_error_accepts_plain_string_code() -> None:
    assert "hosting" in friendly_push_error("org_not_provisioned", "x").lower()


def test_friendly_push_error_falls_back_to_message() -> None:
    assert friendly_push_error(ErrorCode.forbidden, "you do not own this agent") == (
        "you do not own this agent"
    )


def test_format_push_failure_includes_step_and_logs() -> None:
    response = PushResponse(
        agent_id="agent-1",
        build_id="build-123",
        image="img",
        status=BuildStatus.failed,
        error="docker build failed: missing Dockerfile",
        failed_step="build image",
        build_log_url="https://console.cloud.google.com/cloud-build/builds/build-123?project=proj",
    )

    text = format_push_failure(response)

    assert "Deployment failed" in text
    assert "Failed during: build image" in text
    assert "docker build failed" in text
    # Console URL (embeds the project id) must NOT be surfaced; a sanitized
    # build reference is shown instead. See de1b80c.
    assert "Build logs:" not in text
    assert "Reference: build build-123" in text
