"""PushClient.teardown — DELETE /v1/agents/{id} → TeardownResponse / PushClientError."""

import httpx
import pytest
import respx
from dooers.protocol.teardown import TeardownResponse

from dooers.cli.push_client import PushClient, PushClientError

BASE = "https://push.test"
A = "550e8400-e29b-41d4-a716-446655440000"


@respx.mock
def test_teardown_parses_response():
    respx.delete(f"{BASE}/v1/agents/{A}").mock(
        return_value=httpx.Response(
            200,
            json={
                "agent_id": A,
                "service_deleted": True,
                "lb_rule_removed": False,
                "service_name": "svc",
            },
        )
    )
    resp = PushClient(BASE, "tok").teardown(A, env="prod")
    assert isinstance(resp, TeardownResponse)
    assert resp.service_deleted is True
    assert resp.lb_rule_removed is False
    assert respx.calls.last.request.url.params["env"] == "prod"


@respx.mock
def test_teardown_raises_on_error_envelope():
    respx.delete(f"{BASE}/v1/agents/{A}").mock(
        return_value=httpx.Response(
            404,
            json={"error_code": "not_found", "message": "agent not found", "correlation_id": "c1"},
        )
    )
    with pytest.raises(PushClientError) as exc:
        PushClient(BASE, "tok").teardown(A)
    assert "agent not found" in str(exc.value)
    assert exc.value.envelope is not None


@respx.mock
def test_teardown_raises_on_non_json_error():
    respx.delete(f"{BASE}/v1/agents/{A}").mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(PushClientError) as exc:
        PushClient(BASE, "tok").teardown(A)
    assert "502" in str(exc.value)
