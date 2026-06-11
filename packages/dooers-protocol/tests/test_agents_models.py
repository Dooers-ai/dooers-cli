import pytest
from pydantic import ValidationError

from dooers_protocol.agents import (
    AgentManifest,
    AgentRecord,
    CreateAgentRequest,
    ProfileConfig,
    WhatsAppConfig,
)


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


# --- new config-field tests ---


def test_manifest_with_new_fields_validates():
    m = AgentManifest(
        protocol_version="2",
        agent_id="550e8400-e29b-41d4-a716-446655440000",
        name="my-agent",
        organization_id="org_1",
        description="A helpful agent",
        message_path="/agent",
        message_scheme="wss",
        whatsapp=WhatsAppConfig(enabled=True, path="/whatsapp/inbound"),
        profile=ProfileConfig(
            summary="Does things",
            image_url="https://example.com/img.png",
            capabilities=["chat"],
            tools=["search"],
            usage_limits=["100/day"],
        ),
    )
    assert m.description == "A helpful agent"
    assert m.message_path == "/agent"
    assert m.message_scheme == "wss"
    assert m.whatsapp is not None
    assert m.whatsapp.enabled is True
    assert m.profile is not None
    assert m.profile.summary == "Does things"
    assert m.profile.capabilities == ["chat"]


def test_manifest_omitting_new_fields_is_backwards_compat():
    """New optional fields must not break manifests that omit them."""
    m = AgentManifest(
        protocol_version="2",
        agent_id="550e8400-e29b-41d4-a716-446655440000",
        name="x",
        organization_id="org_1",
    )
    assert m.description is None
    assert m.message_path is None
    assert m.message_scheme == "wss"
    assert m.whatsapp is None
    assert m.profile is None


def test_manifest_extra_forbid_still_rejects_top_level_unknown():
    with pytest.raises(ValidationError):
        AgentManifest(
            protocol_version="2",
            agent_id="u",
            name="x",
            organization_id="o",
            unknown_top="oops",
        )


def test_whatsapp_config_extra_forbid():
    with pytest.raises(ValidationError):
        WhatsAppConfig(enabled=True, path="/x", bogus="nope")


def test_profile_config_extra_forbid():
    with pytest.raises(ValidationError):
        ProfileConfig(summary="hi", unknown_field="oops")


def test_manifest_message_scheme_default_is_wss():
    m = AgentManifest(
        protocol_version="2",
        agent_id="u",
        name="x",
        organization_id="o",
    )
    assert m.message_scheme == "wss"


def test_manifest_message_scheme_https_accepted():
    m = AgentManifest(
        protocol_version="2",
        agent_id="u",
        name="x",
        organization_id="o",
        message_scheme="https",
    )
    assert m.message_scheme == "https"


def test_manifest_message_scheme_invalid_rejected():
    with pytest.raises(ValidationError):
        AgentManifest(
            protocol_version="2",
            agent_id="u",
            name="x",
            organization_id="o",
            message_scheme="ws",  # type: ignore[arg-type]
        )
