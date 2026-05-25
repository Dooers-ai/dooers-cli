"""Agent records, create/update requests, and the dooers.yaml manifest schema."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

Runtime = Literal["python", "node", "docker"]


class AgentRecord(BaseModel):
    """An agent as stored in core (the source of truth)."""

    agent_id: str
    name: str
    owner_user_id: str
    runtime: Runtime = "docker"
    env_required: list[str] = []
    deployed_url: str | None = None
    created_at: datetime
    updated_at: datetime


class CreateAgentRequest(BaseModel):
    """Body of `POST /agents` (sent by CLI `dooers agents create`)."""

    name: str
    runtime: Runtime = "docker"
    env_required: list[str] = []


class AgentManifest(BaseModel):
    """Schema of `dooers.yaml` written by `dooers agents create`.

    Strict mode: unknown fields are rejected to catch typos early.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: str
    agent_id: str
    name: str
    runtime: Runtime = "docker"
    env_required: list[str] = []
