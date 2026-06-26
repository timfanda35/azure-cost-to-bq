from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import date, datetime, timezone

from .base import ExportRun

logger = logging.getLogger(__name__)

_MANIFEST_NAMES = ("manifest.json", "_manifest.json")


class _DownloaderStream:
    """Adapt an Azure ``StorageStreamDownloader`` to a file-like object.

    GCS's ``upload_from_file`` requires a stream exposing both ``read`` and
    ``tell``. The downloader supports ``read`` but not ``tell``, so we track the
    byte offset ourselves and delegate reads to the downloader (no buffering,
    so the upload stays streaming rather than loading the blob into memory).
    """

    def __init__(self, downloader):
        self._downloader = downloader
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        data = self._downloader.read(size)
        self._pos += len(data)
        return data

    def tell(self) -> int:
        return self._pos


def _join(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p)


def _parse_iso(ts: str) -> datetime:
    """Parse an Azure ISO-8601 timestamp into a tz-aware UTC datetime.

    Handles a trailing ``Z`` and fractional seconds with more than 6 digits
    (e.g. ``2025-03-21T21:04:06.5234447Z``), which ``datetime.fromisoformat``
    rejects on Python < 3.11/3.12.
    """
    s = ts.strip()
    s = re.sub(r"Z$", "+00:00", s)
    # Truncate fractional seconds to at most 6 digits.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Last resort: drop fractional seconds entirely.
        s2 = re.sub(r"\.\d+", "", s)
        dt = datetime.fromisoformat(s2)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _month_folder(month: date) -> str:
    last = calendar.monthrange(month.year, month.month)[1]
    first_day = month.replace(day=1)
    last_day = month.replace(day=last)
    return f"{first_day:%Y%m%d}-{last_day:%Y%m%d}"


def _is_manifest(blob_name: str) -> bool:
    return blob_name.rsplit("/", 1)[-1] in _MANIFEST_NAMES


class AzureBlobSource:
    """Reads Cost Management export files from Azure Blob Storage.

    Discovery is driven entirely by the per-run ``manifest.json``: a run folder
    without a manifest is treated as in-progress/failed and ignored, and the
    manifest's ``blobs[]`` is the authoritative list of data files to ingest.
    """

    def __init__(
        self,
        account_url: str,
        container: str,
        *,
        credential=None,
        sas_token: str | None = None,
        connection_string: str | None = None,
        endpoint_url: str | None = None,
        verify_blobs: bool = False,
        container_client=None,
    ):
        self._verify_blobs = verify_blobs
        if container_client is not None:
            self._cc = container_client
            return

        # Imported lazily so unit tests can inject a fake container_client
        # without the azure SDK installed.
        from azure.storage.blob import ContainerClient

        client_url = endpoint_url or account_url
        if connection_string:
            self._cc = ContainerClient.from_connection_string(connection_string, container)
        elif sas_token:
            self._cc = ContainerClient(
                account_url=client_url, container_name=container, credential=sas_token
            )
        else:
            if credential is None:
                from azure.identity import DefaultAzureCredential
                credential = DefaultAzureCredential()
            self._cc = ContainerClient(
                account_url=client_url, container_name=container, credential=credential
            )

    def latest_run(self, root_folder: str, export_name: str, month: date) -> ExportRun:
        """Return the latest completed export run for ``month``.

        Raises FileNotFoundError if no manifest exists under the month folder
        (nothing has been exported yet, or the only run is still in progress).
        """
        prefix = _join(root_folder, export_name, _month_folder(month)) + "/"
        manifests = [
            b.name for b in self._cc.list_blobs(name_starts_with=prefix) if _is_manifest(b.name)
        ]
        if not manifests:
            raise FileNotFoundError(
                f"No manifest under {self._cc.container_name}/{prefix} "
                f"(export '{export_name}' for {month:%Y-%m} not ready)"
            )

        best: ExportRun | None = None
        for manifest_name in manifests:
            run = self._read_manifest(manifest_name)
            if run is None:
                continue
            if best is None or run.submitted_time > best.submitted_time:
                best = run
        if best is None:
            raise FileNotFoundError(
                f"Manifest(s) under {prefix} could not be parsed for {month:%Y-%m}"
            )

        if self._verify_blobs:
            self._assert_blobs_exist(best)
        logger.info("blob.run.selected", extra={
            "log_event": "blob.run.selected",
            "export_name": export_name,
            "partition": f"{month:%Y-%m}",
            "run_id": best.run_id,
            "data_version": best.data_version,
            "blob_count": len(best.blobs),
        })
        return best

    def _read_manifest(self, manifest_name: str) -> ExportRun | None:
        try:
            raw = self._cc.download_blob(manifest_name).readall()
            manifest = json.loads(raw)
        except Exception as exc:  # malformed/partial manifest → ignore this run
            logger.warning("blob.manifest.unreadable", extra={
                "log_event": "blob.manifest.unreadable",
                "manifest": manifest_name,
                "error": str(exc),
            })
            return None

        run_info = manifest.get("runInfo", {})
        export_config = manifest.get("exportConfig", {})
        submitted = run_info.get("submittedTime")
        submitted_time = _parse_iso(submitted) if submitted else datetime.min.replace(tzinfo=timezone.utc)
        blobs = [b["blobName"] for b in manifest.get("blobs", []) if b.get("blobName")]
        return ExportRun(
            run_id=run_info.get("runId", ""),
            submitted_time=submitted_time,
            data_version=export_config.get("dataVersion", ""),
            blobs=blobs,
        )

    def _assert_blobs_exist(self, run: ExportRun) -> None:
        for blob_name in run.blobs:
            if not self._cc.get_blob_client(blob_name).exists():
                raise FileNotFoundError(
                    f"Manifest run {run.run_id} lists missing blob: {blob_name}"
                )

    def stream(self, blob_name: str):
        """Return a file-like stream for a blob (suitable for GCS upload)."""
        return _DownloaderStream(self._cc.download_blob(blob_name))
