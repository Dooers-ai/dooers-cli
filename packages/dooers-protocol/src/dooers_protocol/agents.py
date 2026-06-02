"""Agent records, create requests, and the dooers.yaml manifest schema (core v2)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentRecord(BaseModel):
    """An agent as returned by core v2 (the `data` of /api/v2/agents/:id)."""

    agent_id: str
    name: str
    owner_user_id: str | None = None
    organization_id: str | None = None
    host_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateAgentRequest(BaseModel):
    """Body of `POST /api/v2/agents` (sent by `dooers agents create`)."""

    organization_id: str
    name: str


class AgentManifest(BaseModel):
    """Schema of `dooers.yaml` written by `dooers agents create`."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str
    agent_id: str
    name: str
    organization_id: str
