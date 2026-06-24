"""Agent records, create requests, and the dooers.yaml manifest schema (core v2)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AgentRecord(BaseModel):
    agent_id: str
    name: str
    owner_user_id: str | None = None
    organization_id: str | None = None
    host_url: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateAgentRequest(BaseModel):
    organization_id: str
    name: str


class WhatsAppConfig(BaseModel):
    """Optional WhatsApp inbound. `path` is appended to the deployed host."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    path: str | None = None


class ProfileConfig(BaseModel):
    """Listing/marketing profile (maps to core `profile`)."""
    model_config = ConfigDict(extra="forbid")
    summary: str | None = None
    image_url: str | None = None
    capabilities: list[str] = []
    tools: list[str] = []
    usage_limits: list[str] = []


class SuggestedPrompt(BaseModel):
    """A shortcut shown below the chat greeting."""
    model_config = ConfigDict(extra="forbid")
    title: str
    prompt: str


class UiConfig(BaseModel):
    """Dooers chat-UI chrome (maps to core `settings.dooersUi_settings`)."""
    model_config = ConfigDict(extra="forbid")
    hide_mic: bool | None = None
    hide_textinput: bool | None = None
    hide_attachments: bool | None = None
    suggested_prompts: list[SuggestedPrompt] = []


class AgentManifest(BaseModel):
    """Schema of `dooers.yaml`. Written by `dooers agents create`, applied by `dooers push`."""
    model_config = ConfigDict(extra="forbid")
    protocol_version: str
    agent_id: str
    name: str
    organization_id: str
    description: str | None = None
    # Path your agent serves the SDK messages endpoint on (e.g. "/" or "/agent").
    # Combined with the deployed host → serverConfig.apiMessagesUrl.
    message_path: str | None = None
    message_scheme: Literal["wss", "https"] = "wss"
    # Opt into Dooers managed hosting. The org's `hosting` plan feature is the
    # authoritative gate (enforced by dooers-push); this is declarative + a
    # fast client-side guard in `dooers push`.
    hosting: bool = True
    whatsapp: WhatsAppConfig | None = None
    profile: ProfileConfig | None = None
    ui: UiConfig | None = None
