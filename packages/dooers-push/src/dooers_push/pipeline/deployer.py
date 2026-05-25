"""Deployer step — Cloud Build trigger + Cloud Run deploy + URL describe.

POC scaffold. Ports the existing logic from server/main.py in the v1
deploy-service repo. Service name: `{agent_id}-{env}`. Resources labeled
with `agent_id` and `owner_user_id` for future billing attribution.
"""

import logging

from dooers_protocol.push import BuildStatus
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult

logger = logging.getLogger(__name__)


class DeployerStep(PipelineStep):
    name = "deployer"

    async def run(self, ctx: PipelineContext) -> StepResult:
        logger.info("deployer stub: would trigger Cloud Build (agent=%s)", ctx.agent.agent_id)
        return StepResult(
            status=BuildStatus.failed,
            error="scaffold — deployer not yet implemented",
        )
