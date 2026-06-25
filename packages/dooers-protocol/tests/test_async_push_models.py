"""Tests for the async-push status models added in protocol 0.5.0."""

from dooers.protocol.audit import AuditReport
from dooers.protocol.push import (
    BuildStatus,
    BuildStatusResponse,
    PushAcceptedResponse,
    is_terminal,
)


def test_build_status_has_building_and_deploying() -> None:
    assert BuildStatus.building.value == "building"
    assert BuildStatus.deploying.value == "deploying"


def test_push_accepted_response_roundtrip() -> None:
    resp = PushAcceptedResponse(
        build_id="build-1",
        agent_id="ag-1",
        audit=AuditReport(passed=True),
    )
    assert PushAcceptedResponse.model_validate(resp.model_dump()) == resp


def test_push_accepted_response_defaults_to_building() -> None:
    resp = PushAcceptedResponse(build_id="build-1", agent_id="ag-1")
    assert resp.status == BuildStatus.building
    assert resp.audit is None


def test_build_status_response_roundtrip() -> None:
    resp = BuildStatusResponse(
        build_id="build-1",
        agent_id="ag-1",
        status=BuildStatus.failed,
        phase="cloud_build",
        url=None,
        error="docker build failed: missing Dockerfile",
        failed_step="build image",
        error_class="user",
        audit=AuditReport(passed=True),
        correlation_id="corr-1",
    )
    assert BuildStatusResponse.model_validate(resp.model_dump()) == resp


def test_build_status_response_minimal() -> None:
    resp = BuildStatusResponse(
        build_id="build-1",
        agent_id="ag-1",
        status=BuildStatus.building,
    )
    assert resp.phase is None
    assert resp.url is None
    assert resp.error is None
    assert resp.failed_step is None
    assert resp.error_class is None
    assert resp.audit is None
    assert resp.correlation_id is None


def test_is_terminal() -> None:
    assert is_terminal(BuildStatus.succeeded)
    assert is_terminal(BuildStatus.failed)
    assert not is_terminal(BuildStatus.queued)
    assert not is_terminal(BuildStatus.building)
    assert not is_terminal(BuildStatus.deploying)
