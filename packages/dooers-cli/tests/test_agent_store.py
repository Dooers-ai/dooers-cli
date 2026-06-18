# packages/dooers-cli/tests/test_agent_store.py
import httpx
import respx
from dooers.protocol.agents import CreateAgentRequest

from dooers.cli.agent_store import HTTPCoreAgentStore

BASE = "https://core.test"
A = "550e8400-e29b-41d4-a716-446655440000"


@respx.mock
def test_create_posts_org_and_name():
    data = {"agentId": A, "name": "x", "organizationId": "o1", "ownerUserId": "u1"}
    route = respx.post(f"{BASE}/api/v2/agents").mock(
        return_value=httpx.Response(201, json={"success": True, "data": data})
    )
    rec = HTTPCoreAgentStore(BASE, "tok").create(CreateAgentRequest(organization_id="o1", name="x"))
    assert rec.agent_id == A
    assert route.calls.last.request.read() == b'{"organizationId": "o1", "name": "x"}'


@respx.mock
def test_list_by_org():
    respx.get(f"{BASE}/api/v2/agents/organization/o1").mock(return_value=httpx.Response(
        200, json={"success": True, "data": [{"agentId": A, "name": "x", "organizationId": "o1"}]}))
    recs = HTTPCoreAgentStore(BASE, "tok").list_by_org("o1")
    assert recs[0].agent_id == A


@respx.mock
def test_update_patches_agent_and_returns_record():
    data = {"agentId": A, "name": "x", "organizationId": "o1", "ownerUserId": "u1"}
    route = respx.patch(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(200, json={"success": True, "data": data})
    )
    patch_body = {"description": "updated", "serverConfig": {"apiMessagesUrl": "wss://host/path"}}
    rec = HTTPCoreAgentStore(BASE, "tok").update(A, patch_body)
    assert rec.agent_id == A
    # Verify the request body was sent correctly
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent["description"] == "updated"
    assert sent["serverConfig"]["apiMessagesUrl"] == "wss://host/path"
