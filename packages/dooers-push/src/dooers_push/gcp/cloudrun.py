"""Cloud Run service URL lookup."""

import logging

from google.cloud import run_v2

logger = logging.getLogger(__name__)


async def describe_service_url(project_id: str, region: str, service_name: str) -> str:
    """Fetch the live URL of a Cloud Run service.

    Returns e.g. 'https://my-agent-prod-xxx.run.app'.
    """
    client = run_v2.ServicesAsyncClient()
    name = f"projects/{project_id}/locations/{region}/services/{service_name}"
    service = await client.get_service(name=name)
    url = service.uri or ""
    if not url:
        raise RuntimeError(f"service {service_name} has no URI yet")
    logger.info("resolved service url: %s -> %s", service_name, url)
    return url
