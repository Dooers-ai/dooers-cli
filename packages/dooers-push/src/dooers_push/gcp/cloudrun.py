"""Cloud Run service URL lookup.

POC scaffold.
"""


async def describe_service_url(project_id: str, region: str, service_name: str) -> str:
    """Fetch the live URL of a Cloud Run service.

    Returns e.g. 'https://my-agent-prod-xxx.run.app'.
    """
    raise NotImplementedError("scaffold")
