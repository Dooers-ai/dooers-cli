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
async def test_update_url_map_appends_when_host_missing() -> None:
    lb = LBManager(_settings())

    # Mock the existing URL map (no rules for our host yet)
    existing_url_map = MagicMock()
    existing_url_map.host_rules = []
    existing_url_map.path_matchers = []
    existing_url_map.default_service = "default-bs"

    mock_client = MagicMock()
    mock_client.get.return_value = existing_url_map
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.patch.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        await lb._update_url_map(
            "ag_7q4r", "dev",
            host="ag-7q4r-dev.agents.dooers.ai",
            bs_self_link="bs-url",
        )

    mock_client.patch.assert_called_once()
    args, kwargs = mock_client.patch.call_args
    patched = kwargs["url_map_resource"]
    assert len(patched.host_rules) == 1
    assert patched.host_rules[0].hosts == ["ag-7q4r-dev.agents.dooers.ai"]
    assert patched.host_rules[0].path_matcher == "agent-ag-7q4r-dev-pm"
    assert len(patched.path_matchers) == 1
    assert patched.path_matchers[0].name == "agent-ag-7q4r-dev-pm"
    assert patched.path_matchers[0].default_service == "bs-url"


@pytest.mark.asyncio
async def test_update_url_map_is_noop_when_host_already_routed() -> None:
    from google.cloud import compute_v1 as compute_v1_real

    lb = LBManager(_settings())

    # Pre-existing host rule + path matcher for the same agent.
    existing_host_rule = compute_v1_real.HostRule(
        hosts=["ag-7q4r-dev.agents.dooers.ai"],
        path_matcher="agent-ag-7q4r-dev-pm",
    )
    existing_pm = compute_v1_real.PathMatcher(
        name="agent-ag-7q4r-dev-pm",
        default_service="bs-url",
    )

    existing_url_map = MagicMock()
    existing_url_map.host_rules = [existing_host_rule]
    existing_url_map.path_matchers = [existing_pm]

    mock_client = MagicMock()
    mock_client.get.return_value = existing_url_map
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.patch.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        await lb._update_url_map(
            "ag_7q4r", "dev",
            host="ag-7q4r-dev.agents.dooers.ai",
            bs_self_link="bs-url",
        )

    # Patch may still be called (with same content) — idempotent.
    # Key assertion: nothing duplicated.
    if mock_client.patch.called:
        patched = mock_client.patch.call_args.kwargs["url_map_resource"]
        host_strings = [h for hr in patched.host_rules for h in hr.hosts]
        assert host_strings.count("ag-7q4r-dev.agents.dooers.ai") == 1


@pytest.mark.asyncio
async def test_update_url_map_raises_lberror_when_map_not_found() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_client.get.side_effect = gcp_exceptions.NotFound("url map not found")

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        with pytest.raises(LBError) as exc_info:
            await lb._update_url_map(
                "ag_7q4r", "dev",
                host="ag-7q4r-dev.agents.dooers.ai",
                bs_self_link="bs-url",
            )
        assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_register_agent_orchestrates_calls_and_returns_url() -> None:
    lb = LBManager(_settings())

    with (
        patch.object(lb, "_ensure_neg", return_value="neg-url") as m_neg,
        patch.object(lb, "_ensure_backend_service", return_value="bs-url") as m_bs,
        patch.object(lb, "_update_url_map", return_value=None) as m_url_map,
    ):
        url = await lb.register_agent("ag_7q4r", "dev")

    m_neg.assert_called_once_with("ag_7q4r", "dev")
    m_bs.assert_called_once_with("ag_7q4r", "dev", "neg-url")
    m_url_map.assert_called_once()
    assert url == "https://ag-7q4r-dev.agents.dooers.ai"


@pytest.mark.asyncio
async def test_register_agent_prod_drops_env_suffix_in_url() -> None:
    lb = LBManager(_settings())

    with (
        patch.object(lb, "_ensure_neg", return_value="neg-url"),
        patch.object(lb, "_ensure_backend_service", return_value="bs-url"),
        patch.object(lb, "_update_url_map", return_value=None),
    ):
        url = await lb.register_agent("ag_7q4r", "prod")

    assert url == "https://ag-7q4r.agents.dooers.ai"
