"""Tests for push failure display helpers shared with the CLI."""

from dooers_protocol.push import BuildStatus, PushResponse, format_push_failure


def test_format_push_failure_includes_step_error_and_logs() -> None:
    response = PushResponse(
        agent_id="agent-1",
        build_id="build-123",
        image="img",
        status=BuildStatus.failed,
        error="metadata.labels: invalid label value",
        failed_step="deploy to Cloud Run",
        build_log_url="https://console.cloud.google.com/cloud-build/builds/build-123?project=proj",
    )

    text = format_push_failure(response)

    assert "Deployment failed" in text
    assert "Failed during: deploy to Cloud Run" in text
    assert "metadata.labels" in text
    assert "Build logs:" in text
