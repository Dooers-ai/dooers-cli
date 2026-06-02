"""LBManager + naming helpers + LBError.

Owns per-agent Load Balancer registration (Serverless NEG + Backend
Service + URL Map path rule).

All operations are idempotent: re-registering the same agent updates
the existing NEG to point at the latest Cloud Run revision; it does
not create duplicates.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
from google.api_core import exceptions as gcp_exceptions
from google.cloud import compute_v1

from dooers_push.gcp.cloudbuild import cloud_run_service_name

if TYPE_CHECKING:
    from dooers_push.settings import Settings

logger = logging.getLogger(__name__)

SHARED_PATH_MATCHER = "agents-pm"


# ---------------------------------------------------------------------------
# Naming helpers (pure functions)
# ---------------------------------------------------------------------------

def _cloud_run_service(agent_id: str, env: str) -> str:
    return cloud_run_service_name(agent_id, env)


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


def path_segment_for(agent_id: str, env: str) -> str:
    """Return the per-agent path segment (no leading slash).

    Prod drops the env suffix; non-prod keeps it.
    path_segment_for('ag_7q4r', 'prod') -> 'ag-7q4r'
    path_segment_for('ag_7q4r', 'dev')  -> 'ag-7q4r-dev'
    """
    safe = safe_agent_id(agent_id)
    return safe if env == "prod" else f"{safe}-{env}"


def neg_name(agent_id: str, env: str) -> str:
    """Internal resource name; keeps env in all envs for easy filtering."""
    return f"agent-{safe_agent_id(agent_id)}-{env}-neg"


def bs_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-bs"


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

class LBManager:
    """Per-agent LB registration. Idempotent on every call."""

    def __init__(self, settings: Settings) -> None:
        self.project_id = settings.gcp_project_id
        self.region = settings.lb_region
        self.url_map_name = settings.lb_url_map
        self.domain = settings.lb_domain

    async def register_agent(self, agent_id: str, env: str) -> str:
        """Wire {agent_id}-{env} Cloud Run into the LB; return the path URL."""
        seg = path_segment_for(agent_id, env)
        neg_url = await self._ensure_neg(agent_id, env)
        bs_url = await self._ensure_backend_service(agent_id, env, neg_url)
        await self._upsert_path_rule(seg, bs_self_link=bs_url)
        return f"https://{self.domain}/{seg}"

    async def unregister_agent(self, agent_id: str, env: str) -> None:
        """Reverse of register_agent. Removes in order: path rule, BS, NEG."""
        seg = path_segment_for(agent_id, env)

        for step_fn, op_name in (
            (lambda: self._remove_path_rule(seg), "remove_path_rule"),
            (lambda: self._delete_backend_service(agent_id, env), "delete_bs"),
            (lambda: self._delete_neg(agent_id, env), "delete_neg"),
        ):
            try:
                await step_fn()
            except gcp_exceptions.NotFound:
                logger.info("lb_op=%s seg=%s already_gone", op_name, seg)
            except gcp_exceptions.GoogleAPIError as e:
                raise LBError(f"unregister failed at {op_name}: {e}",
                              operation=op_name, cause=e) from e

    async def _upsert_path_rule(self, seg: str, *, bs_self_link: str) -> None:
        """Add or replace the path rule routing /{seg} (+ /{seg}/*) to the
        agent's backend, stripping the /{seg} prefix before forwarding."""
        client = compute_v1.UrlMapsClient()
        loop = asyncio.get_running_loop()
        paths = [f"/{seg}", f"/{seg}/*"]

        def _get_and_patch() -> None:
            try:
                url_map = client.get(project=self.project_id, url_map=self.url_map_name)
            except gcp_exceptions.NotFound as e:
                raise LBError(
                    f"URL map {self.url_map_name!r} not found — has devops completed setup?",
                    operation="url_map_get", cause=e,
                ) from e

            pm = next(
                (m for m in url_map.path_matchers if m.name == SHARED_PATH_MATCHER), None
            )
            if pm is None:
                raise LBError(
                    f"path matcher {SHARED_PATH_MATCHER!r} missing — re-run LB setup",
                    operation="url_map_pm_missing",
                )

            new_rule = compute_v1.PathRule(
                paths=paths,
                service=bs_self_link,
                route_action=compute_v1.HttpRouteAction(
                    url_rewrite=compute_v1.UrlRewrite(path_prefix_rewrite="/")
                ),
            )
            existing_idx = next(
                (i for i, r in enumerate(pm.path_rules) if f"/{seg}" in r.paths), None
            )
            if existing_idx is not None:
                pm.path_rules[existing_idx] = new_rule
            else:
                pm.path_rules.append(new_rule)

            op = client.patch(
                project=self.project_id, url_map=self.url_map_name, url_map_resource=url_map
            )
            op.result(timeout=120)

        try:
            await loop.run_in_executor(None, _get_and_patch)
            logger.info("lb_op=upsert_path_rule seg=%s ok", seg)
        except LBError:
            raise
        except gcp_exceptions.PermissionDenied as e:
            raise LBError(f"permission denied patching URL Map: {e}",
                          operation="url_map_patch", cause=e) from e
        except gcp_exceptions.GoogleAPIError as e:
            raise LBError(f"failed to patch URL Map: {e}",
                          operation="url_map_patch", cause=e) from e

    async def _remove_path_rule(self, seg: str) -> None:
        client = compute_v1.UrlMapsClient()
        loop = asyncio.get_running_loop()

        def _patch() -> None:
            url_map = client.get(project=self.project_id, url_map=self.url_map_name)
            pm = next(
                (m for m in url_map.path_matchers if m.name == SHARED_PATH_MATCHER), None
            )
            if pm is None:
                return
            pm.path_rules = [r for r in pm.path_rules if f"/{seg}" not in r.paths]
            op = client.patch(
                project=self.project_id, url_map=self.url_map_name, url_map_resource=url_map
            )
            op.result(timeout=120)

        await loop.run_in_executor(None, _patch)

    async def _delete_backend_service(self, agent_id: str, env: str) -> None:
        name = bs_name(agent_id, env)
        client = compute_v1.BackendServicesClient()
        loop = asyncio.get_running_loop()

        def _delete() -> None:
            op = client.delete(project=self.project_id, backend_service=name)
            op.result(timeout=120)

        await loop.run_in_executor(None, _delete)

    async def _delete_neg(self, agent_id: str, env: str) -> None:
        name = neg_name(agent_id, env)
        client = compute_v1.RegionNetworkEndpointGroupsClient()
        loop = asyncio.get_running_loop()

        def _delete() -> None:
            op = client.delete(
                project=self.project_id,
                region=self.region,
                network_endpoint_group=name,
            )
            op.result(timeout=120)

        await loop.run_in_executor(None, _delete)

    async def wait_until_reachable(self, url: str, timeout_s: int = 90) -> None:
        """Poll the URL until it returns a non-default response or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout_s

        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    response = await client.get(url)
                    if response.status_code < 500:
                        # LB is responding; rule has propagated.
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1.0)

        logger.warning(
            "lb_op=wait_until_reachable url=%s timed_out_after=%ds (LB will propagate shortly)",
            url, timeout_s,
        )

    # ---- internal --------------------------------------------------------

    async def _ensure_neg(self, agent_id: str, env: str) -> str:
        """Create or get the Serverless NEG. Returns its self-link URL."""
        name = neg_name(agent_id, env)
        cloud_run_service = _cloud_run_service(agent_id, env)

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
            # Must match the agents URL map's scheme (a global external
            # Application LB). Without this the API defaults to EXTERNAL
            # (classic), which an EXTERNAL_MANAGED URL map cannot reference.
            load_balancing_scheme="EXTERNAL_MANAGED",
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
