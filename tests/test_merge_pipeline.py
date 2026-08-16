import logging
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from src.merge_pipeline import run_merge
from src.sources.base import ExportRun


def _env():
    return {
        "SOURCE_A_AZURE_STORAGE_ACCOUNT_URL": "https://old.blob.core.windows.net",
        "SOURCE_A_AZURE_STORAGE_CONTAINER": "old-exports",
        "SOURCE_A_AZURE_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;a",
        "SOURCE_A_AZURE_ROOT_FOLDER_PATH": "ea",
        "SOURCE_A_EXPORT_NAME": "old-export",
        "SOURCE_B_AZURE_STORAGE_ACCOUNT_URL": "https://new.blob.core.windows.net",
        "SOURCE_B_AZURE_STORAGE_CONTAINER": "new-exports",
        "SOURCE_B_AZURE_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;b",
        "SOURCE_B_AZURE_ROOT_FOLDER_PATH": "ea",
        "SOURCE_B_EXPORT_NAME": "new-export",
        "GCS_BUCKET": "dest-bucket",
        "GCS_DESTINATION_PREFIX": "billing",
        "BQ_PROJECT_ID": "my-project",
        "BQ_DATASET_ID": "billing",
        "BQ_TABLE_ID": "azure_cost_actual",
        "BILLING_SCHEMA": "actual",
    }


def _setenv(monkeypatch, env):
    for k in (
        "SOURCE_A_AZURE_STORAGE_SAS_TOKEN", "SOURCE_B_AZURE_STORAGE_SAS_TOKEN",
        "BQ_CMEK_KEY_NAME",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def _run(run_id, export_name, parts):
    return ExportRun(
        run_id=run_id,
        submitted_time=datetime(2026, 6, 11, tzinfo=timezone.utc),
        data_version="1.0",
        blobs=[f"ea/{export_name}/20260601-20260630/{run_id}/{p}" for p in parts],
    )


def _sources_by_export_name(runs_by_export_name):
    def factory(cfg):
        source = MagicMock()
        source.latest_run.return_value = runs_by_export_name[cfg.export_name]
        source.stream.return_value = MagicMock()
        return source
    return factory


def test_merge_uploads_both_sources_and_loads_once(monkeypatch):
    _setenv(monkeypatch, _env())
    runs = {
        "old-export": _run("run-a", "old-export", ("part_0.parquet",)),
        "new-export": _run("run-b", "new-export", ("part_0.parquet", "part_1.parquet")),
    }

    with patch("src.merge_pipeline._build_source", side_effect=_sources_by_export_name(runs)), \
         patch("src.merge_pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: f"gs://{b}/{d}") as mock_gcs, \
         patch("src.merge_pipeline.run_load_job") as mock_bq:
        result = run_merge("2026-06")

    assert result["skipped"] is False
    assert result["files_a"] == 1
    assert result["files_b"] == 2
    assert result["files"] == 3

    # 3 files uploaded total, split under source=a/ and source=b/ of the same gcs_base
    dests = [c.args[2] for c in mock_gcs.call_args_list]
    assert sum("source=a/" in d for d in dests) == 1
    assert sum("source=b/" in d for d in dests) == 2
    gcs_bases = {d.split("source=")[0] for d in dests}
    assert len(gcs_bases) == 1  # same run_id/month folder for both sources

    # one combined BQ load, not two
    mock_bq.assert_called_once()
    wildcards = mock_bq.call_args.args[0]
    assert isinstance(wildcards, list) and len(wildcards) == 2
    assert wildcards[0].endswith("source=a/*.parquet")
    assert wildcards[1].endswith("source=b/*.parquet")
    assert mock_bq.call_args.kwargs["partition_date"] == date(2026, 6, 1)


def test_merge_calls_latest_run_with_correct_args(monkeypatch):
    _setenv(monkeypatch, _env())
    runs = {
        "old-export": _run("run-a", "old-export", ("part_0.parquet",)),
        "new-export": _run("run-b", "new-export", ("part_0.parquet",)),
    }
    sources = {}

    def factory(cfg):
        source = MagicMock()
        source.latest_run.return_value = runs[cfg.export_name]
        source.stream.return_value = MagicMock()
        sources[cfg.export_name] = source
        return source

    with patch("src.merge_pipeline._build_source", side_effect=factory), \
         patch("src.merge_pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: f"gs://{b}/{d}"), \
         patch("src.merge_pipeline.run_load_job"):
        run_merge("2026-06")

    old_call = sources["old-export"].latest_run.call_args
    assert old_call.args == ("ea", "old-export", date(2026, 6, 1))
    new_call = sources["new-export"].latest_run.call_args
    assert new_call.args == ("ea", "new-export", date(2026, 6, 1))


def test_merge_aborts_when_source_a_manifest_missing(monkeypatch, caplog):
    _setenv(monkeypatch, _env())
    source_a = MagicMock()
    source_a.latest_run.side_effect = FileNotFoundError("not ready")
    source_b = MagicMock()
    source_b.latest_run.return_value = _run("run-b", "new-export", ("part_0.parquet",))

    def factory(cfg):
        return source_a if cfg.export_name == "old-export" else source_b

    with caplog.at_level(logging.WARNING, logger="src.merge_pipeline"), \
         patch("src.merge_pipeline._build_source", side_effect=factory), \
         patch("src.merge_pipeline.upload_to_gcs") as mock_gcs, \
         patch("src.merge_pipeline.run_load_job") as mock_bq:
        result = run_merge("2026-06")

    assert result["skipped"] is True
    assert result["reason"] == "no_export_files"
    assert result["source"] == "a"
    mock_gcs.assert_not_called()
    mock_bq.assert_not_called()
    missing = [r for r in caplog.records if getattr(r, "log_event", None) == "merge.source.missing"]
    assert len(missing) == 1 and missing[0].source == "a"


def test_merge_aborts_when_source_b_manifest_empty(monkeypatch):
    _setenv(monkeypatch, _env())
    source_a = MagicMock()
    source_a.latest_run.return_value = _run("run-a", "old-export", ("part_0.parquet",))
    source_b = MagicMock()
    source_b.latest_run.return_value = ExportRun("run-empty", datetime(2026, 6, 11, tzinfo=timezone.utc), "1.0", [])

    def factory(cfg):
        return source_a if cfg.export_name == "old-export" else source_b

    with patch("src.merge_pipeline._build_source", side_effect=factory), \
         patch("src.merge_pipeline.upload_to_gcs") as mock_gcs, \
         patch("src.merge_pipeline.run_load_job") as mock_bq:
        result = run_merge("2026-06")

    assert result["skipped"] is True
    assert result["reason"] == "empty_manifest"
    assert result["source"] == "b"
    mock_gcs.assert_not_called()
    mock_bq.assert_not_called()


def test_merge_default_label_derived_from_export_names(monkeypatch):
    _setenv(monkeypatch, _env())
    runs = {
        "old-export": _run("run-a", "old-export", ("part_0.parquet",)),
        "new-export": _run("run-b", "new-export", ("part_0.parquet",)),
    }

    with patch("src.merge_pipeline._build_source", side_effect=_sources_by_export_name(runs)), \
         patch("src.merge_pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: f"gs://{b}/{d}"), \
         patch("src.merge_pipeline.run_load_job"):
        result = run_merge("2026-06")

    assert result["label"] == "merge-old-export-new-export"


def test_merge_custom_label(monkeypatch):
    _setenv(monkeypatch, _env())
    runs = {
        "old-export": _run("run-a", "old-export", ("part_0.parquet",)),
        "new-export": _run("run-b", "new-export", ("part_0.parquet",)),
    }

    with patch("src.merge_pipeline._build_source", side_effect=_sources_by_export_name(runs)), \
         patch("src.merge_pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: f"gs://{b}/{d}"), \
         patch("src.merge_pipeline.run_load_job"):
        result = run_merge("2026-06", label="ea-migration")

    assert result["label"] == "ea-migration"
