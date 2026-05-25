"""Auditor step — POC stub.

Future: scan archive for malicious patterns, extract endpoint surface,
detect infra needs. POC: always pass, log nothing of interest.
"""

import logging

from dooers_protocol.audit import AuditReport
from dooers_protocol.push import BuildStatus
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult

logger = logging.getLogger(__name__)


class AuditorStep(PipelineStep):
    name = "auditor"

    async def run(self, ctx: PipelineContext) -> StepResult:
        ctx.audit_report = AuditReport(passed=True, findings=[])
        logger.info("auditor stub: passing through (agent=%s)", ctx.agent.agent_id)
        return StepResult(status=BuildStatus.queued)
