"""FastAPI routes — thin. Logic lives in pipeline/ and gcp/.

POC scaffold — actual implementation lands in the next milestone.
See docs/superpowers/specs/2026-05-26-dooers-cli-v2-design.md §5.3.
"""

from fastapi import FastAPI, File, Query, Request, UploadFile

from dooers_protocol.push import BuildStatus, PushResponse

app = FastAPI(
    title="dooers-push",
    version="0.1.0",
    description="Owns the push pipeline backing `dooers push`.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/push/{agent_id}")
async def push(
    agent_id: str,
    request: Request,
    archive: UploadFile = File(...),
    tag: str = Query("latest"),
    env: str = Query("prod"),
) -> PushResponse:
    """Run the push pipeline for `agent_id` synchronously.

    POC scaffold returns a placeholder failed status. Full flow:
    1. verify session against core
    2. resolve agent_id + ownership check via core
    3. upload archive to GCS
    4. pipeline.run(auditor, provisioner, deployer)
    5. poll Cloud Build until done
    6. describe Cloud Run service URL
    7. PATCH /agents/{id} with url
    8. return PushResponse
    """
    return PushResponse(
        agent_id=agent_id,
        build_id="",
        image="",
        status=BuildStatus.failed,
        error="scaffold — push pipeline not yet implemented",
    )
