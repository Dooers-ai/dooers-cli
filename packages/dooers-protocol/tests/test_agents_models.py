import pytest
from pydantic import ValidationError

from dooers_protocol.agents import AgentManifest, AgentRecord, CreateAgentRequest


def test_agent_record_v2_shape():
    r = AgentRecord(
        agent_id="550e8400-e29b-41d4-a716-446655440000",
        name="x",
        owner_user_id="user_1",
        organization_id="org_1",
        host_url=None,
    )
    assert r.host_url is None
    assert r.organization_id == "org_1"


def test_create_agent_request_requires_org():
    req = CreateAgentRequest(organization_id="org_1", name="x")
    assert req.organization_id == "org_1"


def test_manifest_carries_org_and_uuid():
    m = AgentManifest(
        protocol_version="2",
        agent_id="550e8400-e29b-41d4-a716-446655440000",
        name="x",
        organization_id="org_1",
    )
    assert m.organization_id == "org_1"


def test_manifest_rejects_unknown_field():
    with pytest.raises(ValidationError):
        AgentManifest(
            protocol_version="2",
            agent_id="u",
            name="x",
            organization_id="o",
            runtime="docker",  # removed field -> forbidden
        )
