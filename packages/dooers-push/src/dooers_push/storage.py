"""GCS archive upload. Labels every object with agent_id + owner_user_id."""

import logging
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile
from google.cloud import storage

from dooers_push.settings import Settings

logger = logging.getLogger(__name__)


async def upload_archive(
    settings: Settings,
    agent_id: str,
    archive: UploadFile,
    owner_user_id: str,
) -> str:
    """Stream `archive` to gs://{bucket}/agents/{agent_id}/{ts}-{name}. Returns gs:// URI.

    Labels: agent_id, owner_user_id (for billing attribution).
    """
    # Stream upload to a temp file first (Cloud Storage SDK is sync).
    suffix = ""
    if archive.filename and "." in archive.filename:
        suffix = "." + archive.filename.rsplit(".", 1)[-1]
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        while chunk := await archive.read(1024 * 1024):
            tmp.write(chunk)
        tmp_path = Path(tmp.name)

    ts = int(time.time())
    filename = archive.filename or "archive.tar.gz"
    object_path = f"agents/{agent_id}/{ts}-{filename}"

    client = storage.Client(project=settings.gcp_project_id)
    bucket = client.bucket(settings.bucket_name)
    blob = bucket.blob(object_path)
    blob.metadata = {
        "agent_id": agent_id,
        "owner_user_id": owner_user_id,
        "pushed_at": str(ts),
    }
    blob.upload_from_filename(str(tmp_path))
    tmp_path.unlink(missing_ok=True)

    gcs_uri = f"gs://{settings.bucket_name}/{object_path}"
    logger.info("uploaded archive: %s", gcs_uri)
    return gcs_uri
