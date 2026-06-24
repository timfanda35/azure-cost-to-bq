import logging
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from src.pipeline import run_pipeline, billing_periods
from src.sources.base import ExportRun


def _env():
    return {
        "AZURE_STORAGE_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        "AZURE_STORAGE_CONTAINER": "exports",
        "AZURE_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;x",  # avoids SP/credential
        "AZURE_ROOT_FOLDER_PATH": "ea",
        "GCS_BUCKET": "dest-bucket",
        "GCS_DESTINATION_PREFIX": "billing",
        "BQ_PROJECT_ID": "my-project",
        "BQ_DATASET_ID": "billing",
        "EXPORT_ACTUAL_NAME": "act-export",
        "EXPORT_AMORTIZED_NAME": "amort-export",
        "EXPORT_FOCUS_NAME": "focus-export",
    }


def _setenv(monkeypatch, env):
    for k in (
        "REPORT", "PREVIOUS_MONTHS", "BQ_ENFORCE_SCHEMA",
        "EXPORT_ACTUAL_NAME", "EXPORT_AMORTIZED_NAME", "EXPORT_FOCUS_NAME",
        "AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_SAS_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def _run(run_id="run-x", parts=("part_0.parquet",)):
    return ExportRun(
        run_id=run_id,
        submitted_time=datetime(2026, 6, 11, tzinfo=timezone.utc),
        data_version="1.2-preview",
        blobs=[f"ea/export/20260601-20260630/{run_id}/{p}" for p in parts],
    )


# ── billing_periods ────────────────────────────────────────────────────────────

def test_billing_periods_current_plus_one():
    assert billing_periods(date(2026, 6, 23), previous_months=1) == [date(2026, 5, 1), date(2026, 6, 1)]


def test_billing_periods_wraps_year():
    assert billing_periods(date(2026, 1, 10), previous_months=1) == [date(2025, 12, 1), date(2026, 1, 1)]


def test_billing_periods_zero_previous():
    assert billing_periods(date(2026, 6, 23), previous_months=0) == [date(2026, 6, 1)]


def test_billing_periods_three_previous():
    assert billing_periods(date(2026, 3, 5), previous_months=3) == [
        date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
    ]


# ── run_pipeline ───────────────────────────────────────────────────────────────

def test_pipeline_processes_three_reports_two_months(monkeypatch):
    _setenv(monkeypatch, _env())
    fixed_now = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    expected_run_id = f"20260623-{int(fixed_now.timestamp())}"

    source = MagicMock()
    source.latest_run.return_value = _run()
    source.stream.return_value = MagicMock()

    with patch("src.pipeline.datetime") as mock_dt, \
         patch("src.pipeline._build_source", return_value=source), \
         patch("src.pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: f"gs://{b}/{d}") as mock_gcs, \
         patch("src.pipeline.run_load_job") as mock_bq:
        mock_dt.now.return_value = fixed_now
        result = run_pipeline()

    assert result["run_id"] == expected_run_id
    assert result["reports"] == ["actual", "amortized", "focus"]
    assert result["periods"] == ["2026-05", "2026-06"]
    # 3 reports x 2 months = 6 loads
    assert result["periods_loaded"] == 6
    assert result["periods_skipped"] == 0
    assert mock_bq.call_count == 6

    # latest_run called with (root_folder, export_name, month)
    first_call = source.latest_run.call_args_list[0]
    assert first_call.args[0] == "ea"
    assert first_call.args[1] == "act-export"
    assert first_call.args[2] == date(2026, 5, 1)

    # each BQ load gets a gcs wildcard + month decorator + run_id
    for call in mock_bq.call_args_list:
        assert call.args[0].endswith("/*.parquet")
        assert call.kwargs["partition_date"] in (date(2026, 5, 1), date(2026, 6, 1))
        assert call.kwargs["run_id"] == expected_run_id


def test_pipeline_report_filter(monkeypatch):
    _setenv(monkeypatch, _env())
    fixed_now = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    source = MagicMock()
    source.latest_run.return_value = _run()
    source.stream.return_value = MagicMock()

    with patch("src.pipeline.datetime") as mock_dt, \
         patch("src.pipeline._build_source", return_value=source), \
         patch("src.pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: "gs://x/y"), \
         patch("src.pipeline.run_load_job") as mock_bq:
        mock_dt.now.return_value = fixed_now
        result = run_pipeline(report="focus")

    assert result["reports"] == ["focus"]
    assert mock_bq.call_count == 2  # focus only, 2 months


def test_pipeline_partition_single_month(monkeypatch):
    _setenv(monkeypatch, _env())
    source = MagicMock()
    source.latest_run.return_value = _run()
    source.stream.return_value = MagicMock()

    with patch("src.pipeline._build_source", return_value=source), \
         patch("src.pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: "gs://x/y"), \
         patch("src.pipeline.run_load_job") as mock_bq:
        result = run_pipeline(partition="2026-04", report="actual")

    assert result["periods"] == ["2026-04"]
    assert mock_bq.call_count == 1
    assert mock_bq.call_args.kwargs["partition_date"] == date(2026, 4, 1)


def test_pipeline_uploads_each_manifest_blob(monkeypatch):
    _setenv(monkeypatch, _env())
    fixed_now = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    source = MagicMock()
    source.latest_run.return_value = _run(parts=("part_0.parquet", "part_1.parquet"))
    source.stream.return_value = MagicMock()

    with patch("src.pipeline.datetime") as mock_dt, \
         patch("src.pipeline._build_source", return_value=source), \
         patch("src.pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: f"gs://{b}/{d}") as mock_gcs, \
         patch("src.pipeline.run_load_job"):
        mock_dt.now.return_value = fixed_now
        result = run_pipeline(report="focus", partition="2026-06")

    # 2 parts uploaded
    assert mock_gcs.call_count == 2
    focus_result = result["results"][0]
    assert focus_result["files"] == 2
    assert all("focus-export/data/" in c.args[2] for c in mock_gcs.call_args_list)


def test_pipeline_period_skipped_on_missing_manifest(monkeypatch, caplog):
    _setenv(monkeypatch, _env())
    fixed_now = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    source = MagicMock()
    source.latest_run.side_effect = FileNotFoundError("not ready")

    with caplog.at_level(logging.WARNING, logger="src.pipeline"), \
         patch("src.pipeline.datetime") as mock_dt, \
         patch("src.pipeline._build_source", return_value=source), \
         patch("src.pipeline.upload_to_gcs") as mock_gcs, \
         patch("src.pipeline.run_load_job") as mock_bq:
        mock_dt.now.return_value = fixed_now
        result = run_pipeline(report="actual")

    assert result["periods_loaded"] == 0
    assert result["periods_skipped"] == 2
    mock_gcs.assert_not_called()
    mock_bq.assert_not_called()
    skipped = [r for r in caplog.records if getattr(r, "log_event", None) == "period.skipped"]
    assert len(skipped) == 2
    assert all(r.reason == "no_export_files" for r in skipped)


def test_pipeline_empty_manifest_skips_load(monkeypatch, caplog):
    _setenv(monkeypatch, _env())
    fixed_now = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    empty = ExportRun("run-empty", datetime(2026, 6, 11, tzinfo=timezone.utc), "1.2-preview", [])
    source = MagicMock()
    source.latest_run.return_value = empty

    with caplog.at_level(logging.WARNING, logger="src.pipeline"), \
         patch("src.pipeline.datetime") as mock_dt, \
         patch("src.pipeline._build_source", return_value=source), \
         patch("src.pipeline.upload_to_gcs") as mock_gcs, \
         patch("src.pipeline.run_load_job") as mock_bq:
        mock_dt.now.return_value = fixed_now
        result = run_pipeline(report="actual", partition="2026-06")

    assert result["periods_skipped"] == 1
    mock_gcs.assert_not_called()
    mock_bq.assert_not_called()
    skipped = [r for r in caplog.records if getattr(r, "log_event", None) == "period.skipped"]
    assert skipped and skipped[0].reason == "empty_manifest"


def test_pipeline_logs_started_and_complete(monkeypatch, caplog):
    _setenv(monkeypatch, _env())
    fixed_now = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    expected_run_id = f"20260623-{int(fixed_now.timestamp())}"
    source = MagicMock()
    source.latest_run.return_value = _run()
    source.stream.return_value = MagicMock()

    with caplog.at_level(logging.INFO, logger="src.pipeline"), \
         patch("src.pipeline.datetime") as mock_dt, \
         patch("src.pipeline._build_source", return_value=source), \
         patch("src.pipeline.upload_to_gcs", side_effect=lambda s, b, d, **k: "gs://x/y"), \
         patch("src.pipeline.run_load_job"):
        mock_dt.now.return_value = fixed_now
        run_pipeline(report="focus")

    started = [r for r in caplog.records if getattr(r, "log_event", None) == "pipeline.started"]
    complete = [r for r in caplog.records if getattr(r, "log_event", None) == "pipeline.complete"]
    assert len(started) == 1 and started[0].run_id == expected_run_id
    assert started[0].reports == ["focus"]
    assert len(complete) == 1 and complete[0].periods_loaded == 2
