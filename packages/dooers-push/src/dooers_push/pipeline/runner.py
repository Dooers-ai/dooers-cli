"""Sequential pipeline runner. Stops on first failure."""

from collections.abc import Sequence

from dooers_protocol.push import BuildStatus
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult


async def run_pipeline(ctx: PipelineContext, steps: Sequence[PipelineStep]) -> StepResult:
    """Run `steps` in order. First non-success short-circuits."""
    last: StepResult = StepResult(status=BuildStatus.queued)
    for step in steps:
        last = await step.run(ctx)
        if last.status == BuildStatus.failed:
            return last
    return last
