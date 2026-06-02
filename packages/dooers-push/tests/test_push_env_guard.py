"""hostUrl is recorded in core only for prod deploys (single-valued field)."""

from unittest.mock import AsyncMock, MagicMock, patch

from dooers_protocol.agents import AgentRecord
from dooers_protocol.auth import AuthSession
from dooers_protocol.push import BuildStatus
from fastapi.testclient import TestClient

from dooers_push.main import app
from dooers_push.pipeline.base import StepResult

UUID = "550e8400-e29b-41d4-a716-446655440000"


def _core_mock() -> MagicMock:
    core = MagicMock()
    core.get_agent = AsyncMock(
        return_value=AgentRecord(
            agent_id=UUID, name="x", owner_user_id="u1", organization_id="o1"
        )
    )
    core.patch_host_url = AsyncMock()
    return core


async def _fake_pipeline(ctx, steps):  # noqa: ANN001 — test stub
    ctx.build_id = "b1"
    ctx.image = "img"
    ctx.lb_url = f"https://agents.dooers.ai/{UUID}"
    return StepResult(status=BuildStatus.succeeded)


def _push(env: str, core: MagicMock):
    with (
        patch(
            "dooers_push.main.verify_session",
            AsyncMock(return_value=AuthSession(user_id="u1", email="a@b.c")),
        ),
        patch("dooers_push.main.CoreClient", return_value=core),
        patch(
            "dooers_push.main.storage.upload_archive",
            AsyncMock(return_value="gs://b/o"),
        ),
        patch("dooers_push.main.run_pipeline", new=_fake_pipeline),
    ):
        client = TestClient(app)
        return client.post(
            f"/v1/push/{UUID}?env={env}",
            files={"archive": ("a.tar.gz", b"x", "application/gzip")},
            headers={"Authorization": "Bearer t"},
        )


def test_host_url_recorded_for_prod() -> None:
    core = _core_mock()
    resp = _push("prod", core)
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    core.patch_host_url.assert_awaited_once()


def test_host_url_not_recorded_for_non_prod() -> None:
    core = _core_mock()
    resp = _push("dev", core)
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    core.patch_host_url.assert_not_awaited()
