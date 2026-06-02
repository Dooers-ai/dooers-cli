"""Cloud Build trigger + polling. Ports v1 trigger logic and adds polling.

Reference v1: ../../../../../deploy-service/server/main.py
_trigger_cloud_build_with_gcs_source()
"""

import asyncio
import logging

from google.cloud.devtools import cloudbuild_v1

logger = logging.getLogger(__name__)


def _build_deploy_script(
    *,
    service_name: str,
    image: str,
    region: str,
    project_id: str,
    base_env_vars_str: str,
    agent_id: str,
    owner_user_id: str,
    env: str,
) -> str:
    """Bash script merging env.{env} / .env with base vars, then deploying.

    Uses --no-invoker-iam-check (not --allow-unauthenticated) so agents are
    publicly reachable through the LB even when the org enforces Domain
    Restricted Sharing (iam.allowedPolicyMemberDomains), which rejects the
    allUsers binding that --allow-unauthenticated tries to set.
    """
    return f"""#!/bin/bash
set -e
AGENT_ENV_VARS=""
parse_env_file() {{
    local file="$$1"
    if [ -f "$$file" ]; then
        while IFS= read -r line || [ -n "$$line" ]; do
            line=$$(echo "$$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$$//')
            if [[ -n "$$line" && ! "$$line" =~ ^# ]]; then
                line=$$(echo "$$line" | sed 's/#.*$$//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$$//')
                if [[ -n "$$line" && "$$line" =~ = ]]; then
                    if [ -z "$$AGENT_ENV_VARS" ]; then
                        AGENT_ENV_VARS="$$line"
                    else
                        AGENT_ENV_VARS="$$AGENT_ENV_VARS,$$line"
                    fi
                fi
            fi
        done < "$$file"
    fi
}}
# Try env.{env} file first (if creator provides per-env values), then .env as fallback.
[ -f "env.{env}" ] && parse_env_file "env.{env}"
[ -f ".env" ] && parse_env_file ".env"
ALL_ENV_VARS="{base_env_vars_str}"
if [ -n "$$AGENT_ENV_VARS" ]; then
    ALL_ENV_VARS="$$ALL_ENV_VARS,$$AGENT_ENV_VARS"
fi
gcloud run deploy {service_name} \\
    --image={image} --region={region} --platform=managed \\
    --no-invoker-iam-check \\
    --service-account=agent-deploy-service@{project_id}.iam.gserviceaccount.com \\
    --set-env-vars="$$ALL_ENV_VARS" \\
    --labels=agent_id={agent_id},owner_user_id={owner_user_id},env={env} \\
    --cpu=1 --memory=512Mi --min-instances=1 --max-instances=3 \\
    --timeout=300 --cpu-boost"""


def cloud_run_service_name(agent_id: str, env: str) -> str:
    """Cloud Run service name. Letter-prefixed so it's valid even when
    agent_id is a UUID that starts with a digit. Lowercased; '_' → '-'."""
    safe = agent_id.lower().replace("_", "-")
    return f"agent-{safe}-{env}"


def _service_name(agent_id: str, env: str) -> str:
    return cloud_run_service_name(agent_id, env)


def trigger_build(
    *,
    project_id: str,
    gcs_uri: str,
    agent_id: str,
    owner_user_id: str,
    region: str,
    artifact_repo: str,
    env: str,
    tag: str,
) -> tuple[str, str]:
    """Create the Cloud Build that does: docker build → push → gcloud run deploy.

    Returns (operation_name, image_uri).
    """
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"invalid gcs uri: {gcs_uri}")
    _, rest = gcs_uri.split("gs://", 1)
    bucket, object_path = rest.split("/", 1)

    service_name = _service_name(agent_id, env)
    image = f"{region}-docker.pkg.dev/{project_id}/{artifact_repo}/{service_name}:{tag}"

    base_env_vars = {
        "GCP_PROJECT_ID": project_id,
        "GCP_REGION": region,
        "ENVIRONMENT": env,
    }
    base_env_vars_str = ",".join(f"{k}={v}" for k, v in base_env_vars.items())
    deploy_script = _build_deploy_script(
        service_name=service_name,
        image=image,
        region=region,
        project_id=project_id,
        base_env_vars_str=base_env_vars_str,
        agent_id=agent_id,
        owner_user_id=owner_user_id,
        env=env,
    )

    source = cloudbuild_v1.Source(
        storage_source=cloudbuild_v1.StorageSource(bucket=bucket, object_=object_path)
    )
    service_account = (
        f"projects/{project_id}/serviceAccounts/"
        f"agent-deploy-service@{project_id}.iam.gserviceaccount.com"
    )
    build = cloudbuild_v1.Build(
        source=source,
        steps=[
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=["build", "-t", image, "."],
            ),
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=["push", image],
            ),
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/gcloud",
                entrypoint="bash",
                args=["-c", deploy_script],
            ),
        ],
        images=[image],
        service_account=service_account,
        tags=[f"agent-{agent_id}", f"owner-{owner_user_id}", f"env-{env}"],
        options=cloudbuild_v1.BuildOptions(
            machine_type=cloudbuild_v1.BuildOptions.MachineType.N1_HIGHCPU_8,
            logging="CLOUD_LOGGING_ONLY",
        ),
        timeout={"seconds": 1800},
    )

    client = cloudbuild_v1.services.cloud_build.CloudBuildClient()  # type: ignore[attr-defined]
    op = client.create_build(project_id=project_id, build=build)
    # Extract build id from operation metadata
    metadata = cloudbuild_v1.BuildOperationMetadata()
    op.metadata._pb.Unpack(metadata._pb)  # type: ignore[attr-defined]
    build_id = metadata.build.id
    logger.info("triggered cloud build: id=%s image=%s", build_id, image)
    return build_id, image


async def wait_for_build(build_id: str, project_id: str, *, timeout_s: int = 540) -> bool:
    """Poll the Cloud Build until done. Returns True on success, False on failure.

    Raises TimeoutError beyond `timeout_s`.
    """
    from google.cloud.devtools.cloudbuild_v1.types import Build

    client = cloudbuild_v1.services.cloud_build.CloudBuildAsyncClient()  # type: ignore[attr-defined]
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        b = await client.get_build(project_id=project_id, id=build_id)
        if b.status in (Build.Status.SUCCESS,):
            return True
        if b.status in (
            Build.Status.FAILURE,
            Build.Status.INTERNAL_ERROR,
            Build.Status.TIMEOUT,
            Build.Status.CANCELLED,
            Build.Status.EXPIRED,
        ):
            return False
        await asyncio.sleep(5)
    raise TimeoutError(f"build {build_id} did not complete within {timeout_s}s")
