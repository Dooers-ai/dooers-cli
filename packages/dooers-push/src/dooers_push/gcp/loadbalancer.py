"""LBManager + naming helpers + LBError.

Owns per-agent Load Balancer registration (Serverless NEG + Backend
Service + URL Map host rule).

All operations are idempotent: re-registering the same agent updates
the existing NEG to point at the latest Cloud Run revision; it does
not create duplicates.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Naming helpers (pure functions)
# ---------------------------------------------------------------------------

def safe_agent_id(agent_id: str) -> str:
    """Convert an agent_id to a DNS- and GCP-safe form.

    'ag_7q4r' → 'ag-7q4r'.  Lowercases, replaces underscores.
    Raises ValueError on empty input or input containing whitespace.
    """
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if any(c.isspace() for c in agent_id):
        raise ValueError(f"agent_id must not contain whitespace: {agent_id!r}")
    return agent_id.lower().replace("_", "-")


def host_for(agent_id: str, env: str, lb_domain: str) -> str:
    """Return the per-agent LB hostname.

    Prod drops the env suffix; non-prod keeps it.
    host_for('ag_7q4r', 'prod', 'agents.dooers.ai')
    → 'ag-7q4r.agents.dooers.ai'
    host_for('ag_7q4r', 'dev', 'agents.dooers.ai')
    → 'ag-7q4r-dev.agents.dooers.ai'
    """
    safe = safe_agent_id(agent_id)
    if env == "prod":
        return f"{safe}.{lb_domain}"
    return f"{safe}-{env}.{lb_domain}"


def neg_name(agent_id: str, env: str) -> str:
    """Internal resource name; keeps env in all envs for easy filtering."""
    return f"agent-{safe_agent_id(agent_id)}-{env}-neg"


def bs_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-bs"


def path_matcher_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-pm"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LBError(RuntimeError):
    """Any failure interacting with the LB. Carries GCP error context."""

    def __init__(self, message: str, *, operation: str | None = None,
                 cause: Exception | None = None) -> None:
        super().__init__(message)
        self.operation = operation
        self.cause = cause


# ---------------------------------------------------------------------------
# LBManager
# ---------------------------------------------------------------------------

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
from google.api_core import exceptions as gcp_exceptions
from google.cloud import compute_v1

if TYPE_CHECKING:
    from dooers_push.settings import Settings

logger = logging.getLogger(__name__)


class LBManager:
    """Per-agent LB registration. Idempotent on every call."""

    def __init__(self, settings: "Settings") -> None:
        self.project_id = settings.gcp_project_id
        self.region = settings.lb_region
        self.url_map_name = settings.lb_url_map
        self.domain = settings.lb_domain

    async def register_agent(self, agent_id: str, env: str) -> str:
        """Wire {agent_id}-{env} Cloud Run into the LB; return the URL.

        Steps (each idempotent):
        1. Ensure Serverless NEG exists.
        2. Ensure Backend Service exists; attach NEG.
        3. Update URL Map to include host rule + path matcher.
        4. Return the full HTTPS URL.

        Raises LBError on any GCP failure.
        """
        raise NotImplementedError("filled in Task L.7")

    async def unregister_agent(self, agent_id: str, env: str) -> None:
        """Reverse of register_agent. Used on agent delete."""
        raise NotImplementedError("filled in Task L.9")

    async def wait_until_reachable(self, url: str, timeout_s: int = 90) -> None:
        """Poll the URL until it returns a non-default response."""
        raise NotImplementedError("filled in Task L.8")

    # ---- internal --------------------------------------------------------

    async def _ensure_neg(self, agent_id: str, env: str) -> str:
        """Create or get the Serverless NEG. Returns its self-link URL."""
        name = neg_name(agent_id, env)
        cloud_run_service = f"{safe_agent_id(agent_id)}-{env}"

        neg_resource = compute_v1.NetworkEndpointGroup(
            name=name,
            network_endpoint_type="SERVERLESS",
            cloud_run=compute_v1.NetworkEndpointGroupCloudRun(service=cloud_run_service),
        )
        request = compute_v1.InsertRegionNetworkEndpointGroupRequest(
            project=self.project_id,
            region=self.region,
            network_endpoint_group_resource=neg_resource,
        )

        client = compute_v1.RegionNetworkEndpointGroupsClient()
        loop = asyncio.get_running_loop()

        def _insert() -> None:
            op = client.insert(request=request)
            op.result(timeout=120)

        try:
            await loop.run_in_executor(None, _insert)
            logger.info("lb_op=ensure_neg agent_id=%s env=%s neg=created", agent_id, env)
        except gcp_exceptions.Conflict:
            logger.info("lb_op=ensure_neg agent_id=%s env=%s neg=already_exists", agent_id, env)
        except gcp_exceptions.PermissionDenied as e:
            raise LBError(f"permission denied creating NEG {name}: {e}",
                          operation="ensure_neg", cause=e) from e
        except gcp_exceptions.GoogleAPIError as e:
            raise LBError(f"failed to create NEG {name}: {e}",
                          operation="ensure_neg", cause=e) from e

        # Self-link form used by Backend Service refs:
        return (
            f"https://www.googleapis.com/compute/v1/projects/{self.project_id}"
            f"/regions/{self.region}/networkEndpointGroups/{name}"
        )

    async def _ensure_backend_service(self, agent_id: str, env: str, neg_url: str) -> str:
        """Create or get the Backend Service. Returns its self-link URL."""
        name = bs_name(agent_id, env)

        backend = compute_v1.Backend(group=neg_url)
        bs_resource = compute_v1.BackendService(
            name=name,
            protocol="HTTPS",
            backends=[backend],
        )
        request = compute_v1.InsertBackendServiceRequest(
            project=self.project_id,
            backend_service_resource=bs_resource,
        )

        client = compute_v1.BackendServicesClient()
        loop = asyncio.get_running_loop()

        def _insert() -> None:
            op = client.insert(request=request)
            op.result(timeout=120)

        try:
            await loop.run_in_executor(None, _insert)
            logger.info("lb_op=ensure_bs agent_id=%s env=%s bs=created", agent_id, env)
        except gcp_exceptions.Conflict:
            logger.info("lb_op=ensure_bs agent_id=%s env=%s bs=already_exists", agent_id, env)
        except gcp_exceptions.PermissionDenied as e:
            raise LBError(f"permission denied creating BS {name}: {e}",
                          operation="ensure_bs", cause=e) from e
        except gcp_exceptions.GoogleAPIError as e:
            raise LBError(f"failed to create BS {name}: {e}",
                          operation="ensure_bs", cause=e) from e

        return (
            f"https://www.googleapis.com/compute/v1/projects/{self.project_id}"
            f"/global/backendServices/{name}"
        )

    async def _update_url_map(self, agent_id: str, env: str, *,
                              host: str, bs_self_link: str) -> None:
        """Add or update the host rule + path matcher for this agent."""
        pm = path_matcher_name(agent_id, env)
        client = compute_v1.UrlMapsClient()
        loop = asyncio.get_running_loop()

        def _get_and_patch() -> None:
            try:
                url_map = client.get(project=self.project_id, url_map=self.url_map_name)
            except gcp_exceptions.NotFound as e:
                raise LBError(
                    f"URL map {self.url_map_name!r} not found — has devops completed gcp-lb.md setup?",
                    operation="url_map_get",
                    cause=e,
                ) from e

            # Find existing rule for this host (if any)
            existing_pm_idx = None
            existing_hr_idx = None
            for i, pm_obj in enumerate(url_map.path_matchers):
                if pm_obj.name == pm:
                    existing_pm_idx = i
                    break
            for i, hr in enumerate(url_map.host_rules):
                if host in hr.hosts:
                    existing_hr_idx = i
                    break

            # Build new path matcher
            new_pm = compute_v1.PathMatcher(name=pm, default_service=bs_self_link)
            if existing_pm_idx is not None:
                url_map.path_matchers[existing_pm_idx] = new_pm
            else:
                url_map.path_matchers.append(new_pm)

            # Build new host rule
            new_hr = compute_v1.HostRule(hosts=[host], path_matcher=pm)
            if existing_hr_idx is not None:
                url_map.host_rules[existing_hr_idx] = new_hr
            else:
                url_map.host_rules.append(new_hr)

            op = client.patch(
                project=self.project_id,
                url_map=self.url_map_name,
                url_map_resource=url_map,
            )
            op.result(timeout=120)

        try:
            await loop.run_in_executor(None, _get_and_patch)
            logger.info("lb_op=update_url_map agent_id=%s env=%s host=%s ok",
                        agent_id, env, host)
        except LBError:
            raise
        except gcp_exceptions.PermissionDenied as e:
            raise LBError(f"permission denied patching URL Map: {e}",
                          operation="url_map_patch", cause=e) from e
        except gcp_exceptions.GoogleAPIError as e:
            raise LBError(f"failed to patch URL Map: {e}",
                          operation="url_map_patch", cause=e) from e
