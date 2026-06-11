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
    whatsapp: WhatsAppConfig | None = None
    profile: ProfileConfig | None = None
