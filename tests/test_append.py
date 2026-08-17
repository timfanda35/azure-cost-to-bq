import pytest
from unittest.mock import patch
from google.cloud import bigquery
import append


def test_append_requires_partition():
    with pytest.raises(SystemExit) as exc:
        append.main([])
    assert exc.value.code == 2  # argparse usage error


def test_append_exits_zero_on_success():
    with patch("append.run_pipeline", return_value={"run_id": "20260623-1"}) as mock_pipeline:
        with pytest.raises(SystemExit) as exc:
            append.main(["--partition", "2026-06"])
        assert exc.value.code == 0
    mock_pipeline.assert_called_once_with(
        partition="2026-06", write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )


def test_append_exits_one_when_period_skipped():
    skipped_result = {
        "run_id": "20260623-1",
        "periods_loaded": 0,
        "periods_skipped": 1,
        "results": [{"reason": "no_export_files"}],
    }
    with patch("append.run_pipeline", return_value=skipped_result):
        with pytest.raises(SystemExit) as exc:
            append.main(["--partition", "2026-06"])
        assert exc.value.code == 1


def test_append_exits_one_on_failure():
    with patch("append.run_pipeline", side_effect=RuntimeError("boom")):
        with pytest.raises(SystemExit) as exc:
            append.main(["--partition", "2026-06"])
        assert exc.value.code == 1


def test_append_rejects_invalid_partition():
    with pytest.raises(SystemExit) as exc:
        append.main(["--partition", "not-a-month"])
    assert exc.value.code == 2
