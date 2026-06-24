import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.sources.azure_blob import AzureBlobSource, _month_folder, _parse_iso


def _blob(name):
    return SimpleNamespace(name=name)


def _manifest(run_id, submitted, parts, data_version="1.2-preview"):
    return json.dumps({
        "runInfo": {"runId": run_id, "submittedTime": submitted},
        "exportConfig": {"dataVersion": data_version},
        "blobs": [{"blobName": p, "byteCount": 10, "dataRowCount": 1} for p in parts],
    }).encode()


class FakeContainerClient:
    """Minimal stand-in for azure ContainerClient used in unit tests."""

    def __init__(self, listing, manifests):
        self.container_name = "exports"
        self._listing = listing            # list of blob names under the prefix
        self._manifests = manifests        # {manifest_name: bytes}
        self.downloaded = []

    def list_blobs(self, name_starts_with=""):
        return [_blob(n) for n in self._listing if n.startswith(name_starts_with)]

    def download_blob(self, name):
        self.downloaded.append(name)
        dl = MagicMock()
        dl.readall.return_value = self._manifests[name]
        return dl


# ── helpers ───────────────────────────────────────────────────────────────────

def test_month_folder_format():
    assert _month_folder(date(2026, 6, 1)) == "20260601-20260630"
    assert _month_folder(date(2026, 2, 15)) == "20260201-20260228"


def test_parse_iso_handles_z_and_seven_fractional_digits():
    dt = _parse_iso("2025-03-21T21:04:06.5234447Z")
    assert dt.tzinfo is not None
    assert dt.year == 2025 and dt.hour == 21 and dt.second == 6


# ── latest_run ─────────────────────────────────────────────────────────────────

def test_latest_run_picks_newest_by_submitted_time():
    base = "ea/focus-export/20260601-20260630"
    listing = [
        f"{base}/run-old/part_0.parquet",
        f"{base}/run-old/manifest.json",
        f"{base}/run-new/part_0.parquet",
        f"{base}/run-new/part_1.parquet",
        f"{base}/run-new/manifest.json",
    ]
    manifests = {
        f"{base}/run-old/manifest.json": _manifest(
            "run-old", "2026-06-10T01:00:00Z", [f"{base}/run-old/part_0.parquet"]),
        f"{base}/run-new/manifest.json": _manifest(
            "run-new", "2026-06-11T01:00:00Z",
            [f"{base}/run-new/part_0.parquet", f"{base}/run-new/part_1.parquet"]),
    }
    src = AzureBlobSource("https://x", "exports",
                          container_client=FakeContainerClient(listing, manifests))

    run = src.latest_run("ea", "focus-export", date(2026, 6, 1))
    assert run.run_id == "run-new"
    assert run.data_version == "1.2-preview"
    assert run.blobs == [f"{base}/run-new/part_0.parquet", f"{base}/run-new/part_1.parquet"]


def test_latest_run_ignores_folder_without_manifest():
    base = "ea/act-export/20260601-20260630"
    # run-progress has parquet but NO manifest → must be ignored
    listing = [
        f"{base}/run-progress/part_0.parquet",
        f"{base}/run-done/part_0.parquet",
        f"{base}/run-done/manifest.json",
    ]
    manifests = {
        f"{base}/run-done/manifest.json": _manifest(
            "run-done", "2026-06-10T01:00:00Z", [f"{base}/run-done/part_0.parquet"]),
    }
    src = AzureBlobSource("https://x", "exports",
                          container_client=FakeContainerClient(listing, manifests))

    run = src.latest_run("ea", "act-export", date(2026, 6, 1))
    assert run.run_id == "run-done"
    assert run.blobs == [f"{base}/run-done/part_0.parquet"]


def test_latest_run_raises_when_no_manifest():
    base = "ea/act-export/20260601-20260630"
    listing = [f"{base}/run-progress/part_0.parquet"]  # parquet only, no manifest
    src = AzureBlobSource("https://x", "exports",
                          container_client=FakeContainerClient(listing, {}))

    with pytest.raises(FileNotFoundError, match="not ready"):
        src.latest_run("ea", "act-export", date(2026, 6, 1))


def test_latest_run_recognizes_underscore_manifest():
    base = "ea/focus-export/20260601-20260630"
    listing = [f"{base}/run-1/part_0.parquet", f"{base}/run-1/_manifest.json"]
    manifests = {
        f"{base}/run-1/_manifest.json": _manifest(
            "run-1", "2026-06-10T01:00:00Z", [f"{base}/run-1/part_0.parquet"]),
    }
    src = AzureBlobSource("https://x", "exports",
                          container_client=FakeContainerClient(listing, manifests))
    run = src.latest_run("ea", "focus-export", date(2026, 6, 1))
    assert run.run_id == "run-1"


def test_latest_run_uses_correct_prefix():
    cc = MagicMock()
    cc.container_name = "exports"
    cc.list_blobs.return_value = []
    src = AzureBlobSource("https://x", "exports", container_client=cc)
    with pytest.raises(FileNotFoundError):
        src.latest_run("ea/root", "focus-export", date(2026, 6, 1))
    cc.list_blobs.assert_called_once_with(
        name_starts_with="ea/root/focus-export/20260601-20260630/"
    )


def test_stream_delegates_to_download_blob():
    cc = MagicMock()
    cc.container_name = "exports"
    src = AzureBlobSource("https://x", "exports", container_client=cc)
    src.stream("ea/focus-export/20260601-20260630/run-1/part_0.parquet")
    cc.download_blob.assert_called_once_with(
        "ea/focus-export/20260601-20260630/run-1/part_0.parquet"
    )


def test_verify_blobs_raises_on_missing(monkeypatch):
    base = "ea/focus-export/20260601-20260630"
    listing = [f"{base}/run-1/part_0.parquet", f"{base}/run-1/manifest.json"]
    manifests = {
        f"{base}/run-1/manifest.json": _manifest(
            "run-1", "2026-06-10T01:00:00Z", [f"{base}/run-1/part_0.parquet"]),
    }
    cc = FakeContainerClient(listing, manifests)
    missing_client = MagicMock()
    missing_client.exists.return_value = False
    cc.get_blob_client = MagicMock(return_value=missing_client)

    src = AzureBlobSource("https://x", "exports", container_client=cc, verify_blobs=True)
    with pytest.raises(FileNotFoundError, match="missing blob"):
        src.latest_run("ea", "focus-export", date(2026, 6, 1))


def test_endpoint_url_used_for_client(monkeypatch):
    captured = {}

    class FakeCC:
        def __init__(self, account_url=None, container_name=None, credential=None):
            captured["account_url"] = account_url
            captured["container_name"] = container_name

    import src.sources.azure_blob as mod
    fake_module = SimpleNamespace(ContainerClient=FakeCC)
    monkeypatch.setitem(__import__("sys").modules, "azure.storage.blob", fake_module)

    AzureBlobSource(
        "https://acct.blob.core.windows.net", "exports",
        credential=object(),
        endpoint_url="https://acct.privatelink.blob.core.windows.net",
    )
    assert captured["account_url"] == "https://acct.privatelink.blob.core.windows.net"
    assert captured["container_name"] == "exports"
