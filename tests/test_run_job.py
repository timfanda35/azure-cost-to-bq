import pytest
from unittest.mock import patch
import run_job


def _clear(monkeypatch):
    monkeypatch.delenv("PARTITION", raising=False)


def test_job_exits_zero_on_success(monkeypatch):
    _clear(monkeypatch)
    with patch("run_job.run_pipeline", return_value={"run_id": "20260623-1"}) as mock_pipeline:
        with pytest.raises(SystemExit) as exc:
            run_job.main([])
        assert exc.value.code == 0
    mock_pipeline.assert_called_once_with(partition=None)


def test_job_exits_one_on_failure(monkeypatch):
    _clear(monkeypatch)
    with patch("run_job.run_pipeline", side_effect=RuntimeError("boom")):
        with pytest.raises(SystemExit) as exc:
            run_job.main([])
        assert exc.value.code == 1


def test_job_partition_env(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PARTITION", "2026-04")
    with patch("run_job.run_pipeline", return_value={"run_id": "x"}) as mock_pipeline:
        with pytest.raises(SystemExit):
            run_job.main([])
    mock_pipeline.assert_called_once_with(partition="2026-04")


def test_job_cli_overrides_env(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PARTITION", "2026-04")
    with patch("run_job.run_pipeline", return_value={"run_id": "x"}) as mock_pipeline:
        with pytest.raises(SystemExit):
            run_job.main(["--partition", "2026-05"])
    mock_pipeline.assert_called_once_with(partition="2026-05")
