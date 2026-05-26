"""Verify the pipeline runner sequences stubbed steps correctly."""

from datetime import datetime, timezone

import pytest

from dooers_protocol.agents import AgentRecord
from dooers_protocol.auth import AuthSession
from dooers_protocol.push import BuildStatus
from dooers_push.pipeline import (
    AuditorStep,
    PipelineContext,
    ProvisionerStep,
    run_pipeline,
)


@pytest.mark.asyncio
async def test_auditor_and_provisioner_stubs_pass_through() -> None:
    now = datetime.now(timezone.utc)
    ctx = PipelineContext(
        agent=AgentRecord(
            agent_id="ag_test",
            name="test",
            owner_user_id="u_1",
            created_at=now,
            updated_at=now,
        ),
        user=AuthSession(user_id="u_1", email="t@example.com"),
        gcs_uri="local://no-gcs",
        tag="latest",
        env="dev",
    )
    result = await run_pipeline(ctx, [AuditorStep(), ProvisionerStep()])
    assert result.status == BuildStatus.queued
    assert ctx.audit_report is not None
    assert ctx.audit_report.passed
    assert ctx.provisioned_env == {}
