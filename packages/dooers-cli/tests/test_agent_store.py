# packages/dooers-cli/tests/test_agent_store.py
import httpx
import pytest
import respx
from dooers.protocol.agents import CreateAgentRequest

from dooers.cli.agent_store import AgentStoreError, HTTPCoreAgentStore

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


@respx.mock
def test_delete_succeeds_without_data_key():
    # Core returns {success, message} with NO data key — must not call _record().
    respx.delete(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(200, json={"success": True, "message": "Agent deleted"})
    )
    HTTPCoreAgentStore(BASE, "tok").delete(A)  # should not raise


@respx.mock
def test_delete_surfaces_core_error_message():
    respx.delete(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(
            422, json={"success": False, "error": {"message": "cannot delete active agents"}}
        )
    )
    with pytest.raises(AgentStoreError) as exc:
        HTTPCoreAgentStore(BASE, "tok").delete(A)
    assert "cannot delete active agents" in str(exc.value)


@respx.mock
def test_archive_posts_to_archive_route():
    route = respx.post(f"{BASE}/api/v2/agents/{A}/archive").mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"agentId": A, "name": "x"}}
        )
    )
    HTTPCoreAgentStore(BASE, "tok").archive(A)  # should not raise
    assert route.called


@respx.mock
def test_archive_surfaces_core_error_message():
    respx.post(f"{BASE}/api/v2/agents/{A}/archive").mock(
        return_value=httpx.Response(
            422,
            json={"success": False, "error": {"message": "already archived"}},
        )
    )
    with pytest.raises(AgentStoreError) as exc:
        HTTPCoreAgentStore(BASE, "tok").archive(A)
    assert "already archived" in str(exc.value)


@respx.mock
def test_get_populates_status():
    respx.get(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"agentId": A, "name": "x", "status": "active"}}
        )
    )
    rec = HTTPCoreAgentStore(BASE, "tok").get(A)
    assert rec.status == "active"


@respx.mock
def test_delete_nonjson_403_raises_clean_error_not_jsondecode():
    # Core returns a framework-level 403 with a plain-text body (not JSON).
    respx.delete(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    store = HTTPCoreAgentStore(BASE, "tok")
    with pytest.raises(AgentStoreError) as ei:
        store.delete(A)
    msg = str(ei.value)
    assert "403" in msg and "Forbidden" in msg  # clean message, no JSONDecodeError


@respx.mock
def test_delete_empty_success_body_does_not_crash():
    # A 204/empty success body must not blow up json() parsing.
    respx.delete(f"{BASE}/api/v2/agents/{A}").mock(return_value=httpx.Response(204))
    store = HTTPCoreAgentStore(BASE, "tok")
    store.delete(A)  # should simply return without raising
