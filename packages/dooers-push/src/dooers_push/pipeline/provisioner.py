"""Provisioner step — POC stub.

Future: given InfraManifest, provision DB schema / Redis namespace /
RAG indexes / LLM-token bag, return env vars to inject into Cloud Run.
POC: no-op.
"""

import logging

from dooers_protocol.push import BuildStatus
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult

logger = logging.getLogger(__name__)


class ProvisionerStep(PipelineStep):
    name = "provisioner"

    async def run(self, ctx: PipelineContext) -> StepResult:
        ctx.provisioned_env = {}
        logger.info("provisioner stub: no infra (agent=%s)", ctx.agent.agent_id)
        return StepResult(status=BuildStatus.queued)
