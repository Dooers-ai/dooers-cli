"""Async push client: 202 accept, status polling, transient-error mapping."""

import tempfile
from pathlib import Path

import httpx
import pytest
import respx
from dooers.protocol.push import BuildStatus

from dooers.cli.push_client import (
    PushClient,
    PushClientError,
    PushTransientError,
)

BASE = "https://push.test"


def _archive() -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    f.write(b"payload")
    f.close()
    return Path(f.name)


@respx.mock
def test_push_returns_accepted_response_on_202() -> None:
    respx.post(f"{BASE}/v1/push/ag-1").mock(
        return_value=httpx.Response(
            202,
            json={"build_id": "build-9", "agent_id": "ag-1", "status": "building"},
        )
    )
    resp = PushClient(BASE, token="t").push(agent_id="ag-1", archive_path=_archive())
    assert resp.build_id == "build-9"
    assert resp.agent_id == "ag-1"
    assert resp.status == BuildStatus.building


@respx.mock
def test_get_build_status_parses_building() -> None:
    respx.get(f"{BASE}/v1/builds/build-9").mock(
        return_value=httpx.Response(
            200,
            json={
                "build_id": "build-9",
                "agent_id": "ag-1",
                "status": "building",
                "phase": "cloud_build",
            },
        )
    )
    status = PushClient(BASE, token="t").get_build_status("build-9")
    assert status.status == BuildStatus.building
    assert status.phase == "cloud_build"


@respx.mock
def test_get_build_status_parses_failed() -> None:
    respx.get(f"{BASE}/v1/builds/build-9").mock(
        return_value=httpx.Response(
            200,
            json={
                "build_id": "build-9",
                "agent_id": "ag-1",
                "status": "failed",
                "error": "docker build failed: missing Dockerfile",
                "failed_step": "build image",
                "error_class": "user",
            },
        )
    )
    status = PushClient(BASE, token="t").get_build_status("build-9")
    assert status.status == BuildStatus.failed
    assert status.error_class == "user"
    assert status.failed_step == "build image"


@respx.mock
def test_get_build_status_404_raises_client_error() -> None:
    respx.get(f"{BASE}/v1/builds/missing").mock(
        return_value=httpx.Response(
            404,
            json={
                "error_code": "not_found",
                "message": "no such build",
                "correlation_id": "c1",
            },
        )
    )
    with pytest.raises(PushClientError) as exc:
        PushClient(BASE, token="t").get_build_status("missing")
    assert not isinstance(exc.value, PushTransientError)
    assert "no such build" in str(exc.value)


@respx.mock
def test_get_build_status_503_raises_transient() -> None:
    respx.get(f"{BASE}/v1/builds/build-9").mock(
        return_value=httpx.Response(503, text="service unavailable")
    )
    with pytest.raises(PushTransientError):
        PushClient(BASE, token="t").get_build_status("build-9")


@respx.mock
def test_get_build_status_network_error_raises_transient() -> None:
    respx.get(f"{BASE}/v1/builds/build-9").mock(
        side_effect=httpx.ConnectError("boom")
    )
    with pytest.raises(PushTransientError):
        PushClient(BASE, token="t").get_build_status("build-9")


def test_push_transient_error_is_subclass_of_client_error() -> None:
    assert issubclass(PushTransientError, PushClientError)
