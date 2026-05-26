"""Pipeline step interface. Every step implements `run(ctx) -> StepResult`."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from dooers_protocol.agents import AgentRecord
from dooers_protocol.audit import AuditReport
from dooers_protocol.auth import AuthSession
from dooers_protocol.push import BuildStatus


class PipelineContext(BaseModel):
    """Shared state passed between pipeline steps. Steps mutate by attribute."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent: AgentRecord
    user: AuthSession
    gcs_uri: str
    tag: str
    env: str

    # populated by steps as they run:
    audit_report: AuditReport | None = None
    provisioned_env: dict[str, str] = {}
    build_id: str | None = None
    image: str | None = None
    lb_url: str | None = None


class StepResult(BaseModel):
    status: BuildStatus
    error: str | None = None


class PipelineStep(ABC):
    name: str

    @abstractmethod
    async def run(self, ctx: PipelineContext) -> StepResult: ...
