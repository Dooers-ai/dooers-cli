"""Deployer step — Cloud Build → Cloud Run, then LB registration.

POC: ports v1 Cloud Build trigger from server/main.py, then adds the
LB phase via LBManager. Service name: `{agent_id_safe}-{env}` (per the
deploy-time naming in gcp/cloudbuild.py).
"""

import logging

from dooers_protocol.push import BuildStatus
from dooers_push.gcp.cloudbuild import trigger_build, wait_for_build
from dooers_push.gcp.loadbalancer import LBError, LBManager
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult
from dooers_push.settings import Settings

logger = logging.getLogger(__name__)


class DeployerStep(PipelineStep):
    name = "deployer"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lb = LBManager(settings)

    async def run(self, ctx: PipelineContext) -> StepResult:
        # Phase 1: Cloud Build + Cloud Run
        try:
            op_name, image = trigger_build(
                project_id=self.settings.gcp_project_id,
                gcs_uri=ctx.gcs_uri,
                agent_id=ctx.agent.agent_id,
                owner_user_id=ctx.user.user_id,
                region=self.settings.gcp_region,
                artifact_repo=self.settings.artifact_repo,
                env=ctx.env,
                tag=ctx.tag,
            )
            ctx.build_id = op_name
            ctx.image = image
            success = await wait_for_build(op_name)
            if not success:
                return StepResult(status=BuildStatus.failed,
                                  error="Cloud Build reported failure")
        except TimeoutError as e:
            return StepResult(status=BuildStatus.failed, error=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("build phase crashed")
            return StepResult(status=BuildStatus.failed, error=f"build error: {e}")

        # Phase 2: LB registration
        try:
            lb_url = await self.lb.register_agent(ctx.agent.agent_id, ctx.env)
            await self.lb.wait_until_reachable(lb_url)  # warns on timeout, doesn't fail
            ctx.lb_url = lb_url
            return StepResult(status=BuildStatus.succeeded)
        except LBError as e:
            logger.exception("LB registration failed")
            return StepResult(status=BuildStatus.failed,
                              error=f"LB registration failed: {e}")
