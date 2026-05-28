"""Tests for LBManager operations — mocks google-cloud-compute."""

from unittest.mock import MagicMock, patch

import pytest
from google.api_core import exceptions as gcp_exceptions

from dooers_push.gcp.loadbalancer import LBError, LBManager
from dooers_push.settings import Settings


def _settings() -> Settings:
    return Settings(
        gcp_project_id="test-project",
        gcp_region="us-central1",
        bucket_name="test-bucket",
        artifact_repo="agents",
        core_api_url="https://api.test",
        environment="dev",
        request_timeout=10,
        lb_domain="agents.dooers.ai",
        lb_url_map="dooers-agents-url-map",
        lb_region="us-central1",
    )


@pytest.mark.asyncio
async def test_ensure_neg_creates_when_missing() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.insert.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.RegionNetworkEndpointGroupsClient",
        return_value=mock_client,
    ):
        await lb._ensure_neg("ag_7q4r", "dev")

    mock_client.insert.assert_called_once()
    args, kwargs = mock_client.insert.call_args
    request = kwargs["request"]
    assert request.project == "test-project"
    assert request.region == "us-central1"
    assert request.network_endpoint_group_resource.name == "agent-ag-7q4r-dev-neg"
    assert request.network_endpoint_group_resource.network_endpoint_type == "SERVERLESS"


@pytest.mark.asyncio
async def test_ensure_neg_is_noop_when_already_exists() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.side_effect = gcp_exceptions.Conflict("already exists")
    mock_client.insert.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.RegionNetworkEndpointGroupsClient",
        return_value=mock_client,
    ):
        # Should not raise — Conflict is caught and treated as success.
        await lb._ensure_neg("ag_7q4r", "dev")


@pytest.mark.asyncio
async def test_ensure_neg_raises_lberror_on_permission_denied() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.side_effect = gcp_exceptions.PermissionDenied("no perms")
    mock_client.insert.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.RegionNetworkEndpointGroupsClient",
        return_value=mock_client,
    ):
        with pytest.raises(LBError) as exc_info:
            await lb._ensure_neg("ag_7q4r", "dev")
        assert "no perms" in str(exc_info.value).lower() or "permission" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_ensure_bs_creates_when_missing() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.insert.return_value = mock_op

    neg_url = (
        "https://www.googleapis.com/compute/v1/projects/test-project"
        "/regions/us-central1/networkEndpointGroups/agent-ag-7q4r-dev-neg"
    )

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.BackendServicesClient",
        return_value=mock_client,
    ):
        await lb._ensure_backend_service("ag_7q4r", "dev", neg_url)

    mock_client.insert.assert_called_once()
    args, kwargs = mock_client.insert.call_args
    bs = kwargs["request"].backend_service_resource
    assert bs.name == "agent-ag-7q4r-dev-bs"
    assert bs.protocol == "HTTPS"
    assert len(bs.backends) == 1
    assert bs.backends[0].group == neg_url


@pytest.mark.asyncio
async def test_ensure_bs_is_noop_when_already_exists() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.side_effect = gcp_exceptions.Conflict("already exists")
    mock_client.insert.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.BackendServicesClient",
        return_value=mock_client,
    ):
        await lb._ensure_backend_service("ag_7q4r", "dev", "neg-url")
    # No raise = success


@pytest.mark.asyncio
async def test_upsert_path_rule_appends_when_missing() -> None:
    lb = LBManager(_settings())

    pm = MagicMock()
    pm.name = "agents-pm"
    pm.path_rules = []
    existing_url_map = MagicMock()
    existing_url_map.path_matchers = [pm]

    mock_client = MagicMock()
    mock_client.get.return_value = existing_url_map
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.patch.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        await lb._upsert_path_rule("ag-7q4r-dev", bs_self_link="bs-url")

    mock_client.patch.assert_called_once()
    assert len(pm.path_rules) == 1
    rule = pm.path_rules[0]
    assert rule.paths == ["/ag-7q4r-dev", "/ag-7q4r-dev/*"]
    assert rule.service == "bs-url"
    assert rule.route_action.url_rewrite.path_prefix_rewrite == "/"


@pytest.mark.asyncio
async def test_upsert_path_rule_is_idempotent() -> None:
    from google.cloud import compute_v1 as c

    lb = LBManager(_settings())
    existing_rule = c.PathRule(paths=["/ag-7q4r-dev", "/ag-7q4r-dev/*"], service="old-bs")
    pm = MagicMock()
    pm.name = "agents-pm"
    pm.path_rules = [existing_rule]
    existing_url_map = MagicMock()
    existing_url_map.path_matchers = [pm]

    mock_client = MagicMock()
    mock_client.get.return_value = existing_url_map
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.patch.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        await lb._upsert_path_rule("ag-7q4r-dev", bs_self_link="new-bs")

    # Still exactly one rule for this segment (replaced, not duplicated).
    matching = [r for r in pm.path_rules if "/ag-7q4r-dev" in r.paths]
    assert len(matching) == 1
    assert matching[0].service == "new-bs"


@pytest.mark.asyncio
async def test_upsert_path_rule_raises_when_url_map_missing() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_client.get.side_effect = gcp_exceptions.NotFound("no url map")
    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        with pytest.raises(LBError):
            await lb._upsert_path_rule("ag-7q4r-dev", bs_self_link="bs-url")


@pytest.mark.asyncio
async def test_register_agent_returns_path_url_dev() -> None:
    lb = LBManager(_settings())
    with (
        patch.object(lb, "_ensure_neg", return_value="neg-url"),
        patch.object(lb, "_ensure_backend_service", return_value="bs-url"),
        patch.object(lb, "_upsert_path_rule", return_value=None) as m_upsert,
    ):
        url = await lb.register_agent("ag_7q4r", "dev")
    m_upsert.assert_called_once()
    assert url == "https://agents.dooers.ai/ag-7q4r-dev"


@pytest.mark.asyncio
async def test_register_agent_returns_path_url_prod() -> None:
    lb = LBManager(_settings())
    with (
        patch.object(lb, "_ensure_neg", return_value="neg-url"),
        patch.object(lb, "_ensure_backend_service", return_value="bs-url"),
        patch.object(lb, "_upsert_path_rule", return_value=None),
    ):
        url = await lb.register_agent("ag_7q4r", "prod")
    assert url == "https://agents.dooers.ai/ag-7q4r"


@pytest.mark.asyncio
async def test_wait_until_reachable_returns_on_first_success() -> None:
    from unittest.mock import AsyncMock

    lb = LBManager(_settings())

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    mock_client.get.return_value = mock_response

    with patch(
        "dooers_push.gcp.loadbalancer.httpx.AsyncClient",
        return_value=mock_client,
    ):
        # Should return without raising
        await lb.wait_until_reachable("https://ag-test.agents.dooers.ai", timeout_s=5)


@pytest.mark.asyncio
async def test_wait_until_reachable_returns_on_timeout_without_raising() -> None:
    import httpx as httpx_mod

    lb = LBManager(_settings())

    # Mock httpx.AsyncClient where every request raises ConnectError
    class FailingClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, *args, **kwargs):
            raise httpx_mod.ConnectError("nope")

    with patch(
        "dooers_push.gcp.loadbalancer.httpx.AsyncClient",
        return_value=FailingClient(),
    ):
        # Should NOT raise on timeout — logs a warning instead.
        await lb.wait_until_reachable("https://ag-test.agents.dooers.ai", timeout_s=1)


@pytest.mark.asyncio
async def test_unregister_agent_removes_in_correct_order() -> None:
    lb = LBManager(_settings())
    calls: list[str] = []

    async def _rec_path(*a, **k): calls.append("path")
    async def _rec_bs(*a, **k): calls.append("bs")
    async def _rec_neg(*a, **k): calls.append("neg")

    with (
        patch.object(lb, "_remove_path_rule", side_effect=_rec_path),
        patch.object(lb, "_delete_backend_service", side_effect=_rec_bs),
        patch.object(lb, "_delete_neg", side_effect=_rec_neg),
    ):
        await lb.unregister_agent("ag_7q4r", "dev")

    assert calls == ["path", "bs", "neg"]


@pytest.mark.asyncio
async def test_unregister_agent_ignores_missing() -> None:
    lb = LBManager(_settings())

    async def _raise(*a, **k):
        raise gcp_exceptions.NotFound("gone")

    with (
        patch.object(lb, "_remove_path_rule", side_effect=_raise),
        patch.object(lb, "_delete_backend_service", side_effect=_raise),
        patch.object(lb, "_delete_neg", side_effect=_raise),
    ):
        await lb.unregister_agent("ag_7q4r", "dev")  # no raise
