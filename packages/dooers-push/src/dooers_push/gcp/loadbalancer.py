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
