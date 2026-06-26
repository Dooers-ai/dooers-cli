"""TDD tests for manifest_sync.build_agent_patch."""

from dooers.protocol.agents import (
    AgentManifest,
    ProfileConfig,
    SuggestedPrompt,
    UiConfig,
    WhatsAppConfig,
)

from dooers.cli.manifest_sync import build_agent_patch

# Shared base manifest (minimal required fields).
_BASE = dict(
    protocol_version="2",
    agent_id="ag-x",
    name="test-agent",
    organization_id="org_1",
)

DEPLOYED = "https://agents.dooers.ai/ag-x"


def _manifest(**kwargs) -> AgentManifest:
    return AgentManifest(**{**_BASE, **kwargs})


# ---------------------------------------------------------------------------
# message_path / message_scheme
# ---------------------------------------------------------------------------


def test_message_path_default_scheme_wss():
    m = _manifest(message_path="/agent")
    patch = build_agent_patch(m, DEPLOYED)
    assert patch["serverConfig"]["apiMessagesUrl"] == "wss://agents.dooers.ai/ag-x/agent"


def test_message_path_scheme_https():
    m = _manifest(message_path="/agent", message_scheme="https")
    patch = build_agent_patch(m, DEPLOYED)
    assert patch["serverConfig"]["apiMessagesUrl"] == "https://agents.dooers.ai/ag-x/agent"


def test_message_path_no_leading_slash_normalized():
    m = _manifest(message_path="agent")
    patch = build_agent_patch(m, DEPLOYED)
    assert patch["serverConfig"]["apiMessagesUrl"] == "wss://agents.dooers.ai/ag-x/agent"


def test_message_path_root_slash():
    m = _manifest(message_path="/")
    patch = build_agent_patch(m, DEPLOYED)
    # trailing slash stripped from host+seg AND path collapse: "host/ag-x/" -> "host/ag-x"
    url = patch["serverConfig"]["apiMessagesUrl"]
    assert url == "wss://agents.dooers.ai/ag-x"


def test_no_message_path_no_server_config():
    m = _manifest()
    patch = build_agent_patch(m, DEPLOYED)
    assert "serverConfig" not in patch


# ---------------------------------------------------------------------------
# whatsapp
# ---------------------------------------------------------------------------


def test_whatsapp_enabled_with_path():
    m = _manifest(whatsapp=WhatsAppConfig(enabled=True, path="/whatsapp/inbound"))
    patch = build_agent_patch(m, DEPLOYED)
    wa = patch["settings"]["integration_settings"]["whatsapp"]
    assert wa["enabled"] is True
    assert wa["inbound_http_url"] == "https://agents.dooers.ai/ag-x/whatsapp/inbound"


def test_whatsapp_enabled_default_path():
    """When path is None but enabled, defaults to /whatsapp/inbound."""
    m = _manifest(whatsapp=WhatsAppConfig(enabled=True, path=None))
    patch = build_agent_patch(m, DEPLOYED)
    wa = patch["settings"]["integration_settings"]["whatsapp"]
    assert wa["inbound_http_url"] == "https://agents.dooers.ai/ag-x/whatsapp/inbound"


def test_whatsapp_disabled_no_settings_key():
    m = _manifest(whatsapp=WhatsAppConfig(enabled=False, path="/x"))
    patch = build_agent_patch(m, DEPLOYED)
    assert "settings" not in patch


def test_whatsapp_none_no_settings_key():
    m = _manifest()
    patch = build_agent_patch(m, DEPLOYED)
    assert "settings" not in patch


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


def test_profile_maps_snake_to_camel():
    m = _manifest(
        profile=ProfileConfig(
            summary="Does things",
            image_url="https://example.com/img.png",
            capabilities=["chat"],
            tools=["search"],
            usage_limits=["100/day"],
        )
    )
    patch = build_agent_patch(m, DEPLOYED)
    prof = patch["profile"]
    assert prof["summary"] == "Does things"
    assert prof["imageUrl"] == "https://example.com/img.png"
    assert prof["capabilities"] == ["chat"]
    assert prof["tools"] == ["search"]
    assert prof["usageLimits"] == ["100/day"]


def test_profile_empty_no_profile_key():
    """ProfileConfig with all defaults produces no 'profile' key."""
    m = _manifest(profile=ProfileConfig())
    patch = build_agent_patch(m, DEPLOYED)
    assert "profile" not in patch


def test_profile_none_no_profile_key():
    m = _manifest()
    patch = build_agent_patch(m, DEPLOYED)
    assert "profile" not in patch


def test_profile_partial_only_set_fields_included():
    m = _manifest(profile=ProfileConfig(summary="short bio"))
    patch = build_agent_patch(m, DEPLOYED)
    prof = patch["profile"]
    assert prof["summary"] == "short bio"
    assert "imageUrl" not in prof
    assert "capabilities" not in prof


# ---------------------------------------------------------------------------
# description
# ---------------------------------------------------------------------------


def test_description_included_when_set():
    m = _manifest(description="A helpful agent")
    patch = build_agent_patch(m, DEPLOYED)
    assert patch["description"] == "A helpful agent"


def test_description_none_not_in_patch():
    m = _manifest()
    patch = build_agent_patch(m, DEPLOYED)
    assert "description" not in patch


def test_description_empty_string_not_in_patch():
    # Scaffolded-but-unedited description must NOT send null and wipe core's value.
    m = _manifest(description="")
    patch = build_agent_patch(m, DEPLOYED)
    assert "description" not in patch


# ---------------------------------------------------------------------------
# hostUrl — always recorded (the CLI writes it; the async webhook can't)
# ---------------------------------------------------------------------------


def test_minimal_manifest_records_host_url_only():
    m = _manifest()
    patch = build_agent_patch(m, DEPLOYED)
    assert patch == {"hostUrl": DEPLOYED}


def test_host_url_always_present_alongside_other_fields():
    m = _manifest(message_path="/agent", description="A helpful agent")
    patch = build_agent_patch(m, DEPLOYED)
    assert patch["hostUrl"] == DEPLOYED


# ---------------------------------------------------------------------------
# deployed_url variations
# ---------------------------------------------------------------------------


def test_deployed_url_trailing_slash_stripped():
    m = _manifest(message_path="/agent")
    patch = build_agent_patch(m, "https://agents.dooers.ai/ag-x/")
    assert patch["serverConfig"]["apiMessagesUrl"] == "wss://agents.dooers.ai/ag-x/agent"


# ---------------------------------------------------------------------------
# ui → settings.dooersUi_settings
# ---------------------------------------------------------------------------


def test_ui_hide_flags_mapped():
    m = _manifest(ui=UiConfig(hide_mic=True, hide_attachments=False))
    patch = build_agent_patch(m, DEPLOYED)
    ui = patch["settings"]["dooersUi_settings"]
    assert ui == {"hide_mic": True, "hide_attachments": False}
    # hide_textinput was None → omitted
    assert "hide_textinput" not in ui


def test_ui_suggested_prompts_mapped():
    m = _manifest(
        ui=UiConfig(suggested_prompts=[SuggestedPrompt(title="Hi", prompt="say hi")])
    )
    patch = build_agent_patch(m, DEPLOYED)
    assert patch["settings"]["dooersUi_settings"]["suggested_prompts"] == [
        {"title": "Hi", "prompt": "say hi"}
    ]


def test_ui_empty_not_in_patch():
    m = _manifest(ui=UiConfig())
    patch = build_agent_patch(m, DEPLOYED)
    assert "settings" not in patch


def test_ui_and_whatsapp_coexist_under_settings():
    m = _manifest(
        whatsapp=WhatsAppConfig(enabled=True, path="/wa"),
        ui=UiConfig(hide_mic=True),
    )
    patch = build_agent_patch(m, DEPLOYED)
    assert patch["settings"]["integration_settings"]["whatsapp"]["enabled"] is True
    assert patch["settings"]["dooersUi_settings"]["hide_mic"] is True
