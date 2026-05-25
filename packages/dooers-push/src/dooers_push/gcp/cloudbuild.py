"""Cloud Build trigger + polling. Ports v1 logic from server/main.py.

POC scaffold.
"""


async def trigger_build(
    project_id: str,
    gcs_uri: str,
    image: str,
    agent_id: str,
    owner_user_id: str,
    region: str,
    env: str,
) -> str:
    """Create a Cloud Build that does: docker build → push → gcloud run deploy.

    Returns the Cloud Build operation name.
    Build is labeled with agent_id + owner_user_id for billing attribution.
    """
    raise NotImplementedError("scaffold — port _trigger_cloud_build_with_gcs_source")


async def wait_for_build(operation_name: str, *, timeout_s: int = 540) -> bool:
    """Poll Cloud Build until done. Returns True on success, False on failure.

    Hard cap at `timeout_s`; raises TimeoutError beyond that.
    """
    raise NotImplementedError("scaffold")
