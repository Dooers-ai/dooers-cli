"""FastAPI routes. Skinny — logic lives in pipeline/ and gcp/."""

import logging
import os
import uuid

from dooers_protocol.errors import ErrorCode, ErrorEnvelope
from dooers_protocol.push import BuildStatus, PushResponse
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException

from dooers_push import storage
from dooers_push.auth import verify_session
from dooers_push.core_client import CoreClient
from dooers_push.pipeline import (
    AuditorStep,
    DeployerStep,
    PipelineContext,
    ProvisionerStep,
    run_pipeline,
)
from dooers_push.settings import Settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="dooers-push",
    version="0.1.0",
    description="Owns the push pipeline backing `dooers push`.",
)

# TrustedHost middleware. In production, restrict accepted Host headers
# to prevent host header spoofing. TRUSTED_HOSTS is a comma-separated list
# (e.g., "push.dooers.ai,push.dev.dooers.ai"). Defaults to "*" (off) so
# the demo and tests work out of the box.
if os.environ.get("ENVIRONMENT") == "prod":
    trusted_hosts = os.environ.get("TRUSTED_HOSTS", "*").split(",")
    if "*" not in trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

# Rate limiting. In-memory store is fine for Cloud Run — each instance
# enforces independently. Per-IP key. Configurable via env var.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Validate required env vars at startup so misconfiguration fails fast,
# not per-request.
@app.on_event("startup")
async def _validate_env() -> None:
    Settings.from_env()  # raises RuntimeError if any required var missing


def _error_code_for_status(status_code: int) -> ErrorCode:
    return {
        401: ErrorCode.unauthenticated,
        403: ErrorCode.forbidden,
        404: ErrorCode.not_found,
        413: ErrorCode.archive_too_large,
        503: ErrorCode.core_unreachable,
    }.get(status_code, ErrorCode.internal)


@app.exception_handler(StarletteHTTPException)
async def _envelope_http_exception(request, exc: StarletteHTTPException) -> JSONResponse:
    if not request.url.path.startswith("/v1/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    envelope = ErrorEnvelope(
        error_code=_error_code_for_status(exc.status_code),
        message=str(exc.detail),
        correlation_id=str(uuid.uuid4()),
    )
    return JSONResponse(envelope.model_dump(mode="json"), status_code=exc.status_code)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/push/{agent_id}")
@limiter.limit(os.getenv("RATE_LIMIT_PUSH", "10/minute"))
async def push(
    agent_id: str,
    request: Request,
    archive: UploadFile = File(...),
    tag: str = Query("latest"),
    env: str = Query("prod"),
) -> PushResponse:
    """Run the synchronous push pipeline for `agent_id`."""
    correlation_id = str(uuid.uuid4())
    settings = Settings.from_env()
    logger.info("push start: agent_id=%s correlation_id=%s", agent_id, correlation_id)

    if not archive.filename or not archive.filename.endswith((".tar.gz", ".tgz", ".zip")):
        raise HTTPException(status_code=400, detail="archive must be .tar.gz/.tgz/.zip")

    session = await verify_session(request, settings)
    token = request.headers["Authorization"][len("Bearer "):]
    core = CoreClient(base_url=settings.core_api_url, token=token)
    agent = await core.get_agent(agent_id, session)

    gcs_uri = await storage.upload_archive(
        settings, agent_id, archive, owner_user_id=session.user_id
    )

    ctx = PipelineContext(
        agent=agent, user=session, gcs_uri=gcs_uri, tag=tag, env=env,
    )
    result = await run_pipeline(
        ctx, [AuditorStep(), ProvisionerStep(), DeployerStep(settings)]
    )

    if result.status == BuildStatus.failed:
        return PushResponse(
            agent_id=agent_id,
            build_id=ctx.build_id or "",
            image=ctx.image or "",
            status=BuildStatus.failed,
            error=result.error,
            audit=ctx.audit_report,
        )

    # Build + LB registration succeeded → URL comes from ctx.lb_url.
    if not ctx.lb_url:
        # Defensive: should never happen on success path.
        return PushResponse(
            agent_id=agent_id,
            build_id=ctx.build_id or "",
            image=ctx.image or "",
            status=BuildStatus.failed,
            error="internal: deployer reported success but no lb_url set",
            audit=ctx.audit_report,
        )

    try:
        await core.patch_host_url(agent_id, ctx.lb_url)
    except Exception as e:  # noqa: BLE001 — non-fatal: agent is live, URL just not recorded
        logger.warning("patch_host_url failed for %s: %s", agent_id, e)
    return PushResponse(
        agent_id=agent_id,
        build_id=ctx.build_id or "",
        image=ctx.image or "",
        status=BuildStatus.succeeded,
        url=ctx.lb_url,
        audit=ctx.audit_report,
    )
