# packages/dooers-push/tests/test_core_client.py
import httpx
import pytest
import respx
from dooers_protocol.auth import AuthSession
from fastapi import HTTPException

from dooers_push.core_client import CoreClient

BASE = "https://core.test"
A = "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.asyncio
@respx.mock
async def test_get_agent_ok_and_owner() -> None:
    respx.get(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "data": {"agentId": A, "name": "x", "ownerUserId": "u1", "organizationId": "o1"},
            },
        )
    )
    rec = await CoreClient(BASE, "tok").get_agent(A, AuthSession(user_id="u1", email="a@b.c"))
    assert rec.agent_id == A and rec.owner_user_id == "u1"


@pytest.mark.asyncio
@respx.mock
async def test_get_agent_not_owner_403() -> None:
    respx.get(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "data": {"agentId": A, "name": "x", "ownerUserId": "someone_else"},
            },
        )
    )
    with pytest.raises(HTTPException) as e:
        await CoreClient(BASE, "tok").get_agent(A, AuthSession(user_id="u1", email="a@b.c"))
    assert e.value.status_code == 403


@pytest.mark.asyncio
@respx.mock
async def test_patch_host_url_ok() -> None:
    route = respx.patch(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {}})
    )
    await CoreClient(BASE, "tok").patch_host_url(A, "https://agents.dooers.ai/" + A)
    assert route.called
    assert route.calls.last.request.read() == (
        b'{"hostUrl":"https://agents.dooers.ai/' + A.encode() + b'"}'
    )
