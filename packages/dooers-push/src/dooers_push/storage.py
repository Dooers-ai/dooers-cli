"""GCS archive upload.

POC scaffold.
"""

from fastapi import UploadFile


async def upload_archive(agent_id: str, archive: UploadFile, *, owner_user_id: str) -> str:
    """Upload `archive` to gs://<bucket>/agents/{agent_id}/{ts}-<filename>.

    Labels: agent_id, owner_user_id (for billing attribution).
    Returns the gs:// URI.
    """
    raise NotImplementedError("scaffold — port from server/main.py")
