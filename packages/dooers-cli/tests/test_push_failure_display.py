"""Push failure display tests."""

from dooers_protocol.push import BuildStatus, PushResponse, format_push_failure


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
    assert "Build logs:" in text
