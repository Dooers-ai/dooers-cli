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
