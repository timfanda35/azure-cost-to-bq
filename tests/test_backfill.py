import pytest
from unittest.mock import patch
import backfill


def test_month_range_single_month():
    assert backfill.month_range(
        backfill._parse_month("2026-03"), backfill._parse_month("2026-03")
    ) == ["2026-03"]


def test_month_range_spans_year_boundary():
    assert backfill.month_range(
        backfill._parse_month("2025-11"), backfill._parse_month("2026-02")
    ) == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_backfill_calls_run_pipeline_per_month():
    with patch("backfill.run_pipeline", return_value={"run_id": "x"}) as mock_pipeline:
        with pytest.raises(SystemExit) as exc:
            backfill.main(["--start", "2026-01", "--end", "2026-03"])
        assert exc.value.code == 0
    calls = [c.kwargs["partition"] for c in mock_pipeline.call_args_list]
    assert calls == ["2026-01", "2026-02", "2026-03"]


def test_backfill_continues_after_failure_and_exits_zero():
    with patch(
        "backfill.run_pipeline",
        side_effect=[{"run_id": "a"}, RuntimeError("boom"), {"run_id": "c"}],
    ) as mock_pipeline:
        with pytest.raises(SystemExit) as exc:
            backfill.main(["--start", "2026-01", "--end", "2026-03"])
        assert exc.value.code == 0
    calls = [c.kwargs["partition"] for c in mock_pipeline.call_args_list]
    assert calls == ["2026-01", "2026-02", "2026-03"]


def test_backfill_counts_skipped_period_as_not_succeeded():
    with patch(
        "backfill.run_pipeline",
        return_value={
            "run_id": "x",
            "periods_loaded": 0,
            "periods_skipped": 1,
            "results": [{"skipped": True, "reason": "no_export_files"}],
        },
    ) as mock_pipeline:
        with pytest.raises(SystemExit) as exc:
            backfill.main(["--start", "2026-01", "--end", "2026-01"])
        assert exc.value.code == 0
    mock_pipeline.assert_called_once()


def test_backfill_rejects_start_after_end():
    with patch("backfill.run_pipeline") as mock_pipeline:
        with pytest.raises(SystemExit) as exc:
            backfill.main(["--start", "2026-03", "--end", "2026-01"])
        assert exc.value.code == 2
    mock_pipeline.assert_not_called()


def test_backfill_rejects_invalid_month_format():
    with patch("backfill.run_pipeline") as mock_pipeline:
        with pytest.raises(SystemExit) as exc:
            backfill.main(["--start", "2026-13", "--end", "2026-03"])
        assert exc.value.code == 2
    mock_pipeline.assert_not_called()
