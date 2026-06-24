import logging
from google.cloud import storage

logger = logging.getLogger(__name__)


def upload_to_gcs(
    stream,
    gcs_bucket: str,
    dest_blob_name: str,
    *,
    run_id: str = "",
    export_name: str = "",
    partition: str = "",
    source_blob: str = "",
) -> str:
    """Stream upload to GCS and return the gs:// URI."""
    client = storage.Client()
    bucket = client.bucket(gcs_bucket)
    blob = bucket.blob(dest_blob_name)
    blob.upload_from_file(stream, rewind=False)
    gcs_uri = f"gs://{gcs_bucket}/{dest_blob_name}"
    logger.info("gcs.file.uploaded", extra={
        "log_event": "gcs.file.uploaded",
        "run_id": run_id,
        "export_name": export_name,
        "partition": partition,
        "source_blob": source_blob,
        "gcs_uri": gcs_uri,
    })
    return gcs_uri
