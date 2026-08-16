import pytest
from unittest.mock import patch
import merge_sources


def test_merge_sources_calls_run_merge_with_partition_and_label():
    with patch(
        "merge_sources.run_merge",
        return_value={"run_id": "x", "skipped": False, "files": 3, "bq_table": "p.d.t"},
    ) as mock_merge:
        with pytest.raises(SystemExit) as exc:
            merge_sources.main(["--partition", "2026-06", "--label", "ea-migration"])
        assert exc.value.code == 0
    mock_merge.assert_called_once_with("2026-06", label="ea-migration")


def test_merge_sources_default_label_is_none():
    with patch(
        "merge_sources.run_merge",
        return_value={"run_id": "x", "skipped": False, "files": 3, "bq_table": "p.d.t"},
    ) as mock_merge:
        with pytest.raises(SystemExit) as exc:
            merge_sources.main(["--partition", "2026-06"])
        assert exc.value.code == 0
    mock_merge.assert_called_once_with("2026-06", label=None)


def test_merge_sources_exits_nonzero_on_skip():
    with patch(
        "merge_sources.run_merge",
        return_value={"skipped": True, "reason": "no_export_files", "source": "a"},
    ):
        with pytest.raises(SystemExit) as exc:
            merge_sources.main(["--partition", "2026-06"])
        assert exc.value.code != 0


def test_merge_sources_exits_nonzero_on_exception():
    with patch("merge_sources.run_merge", side_effect=RuntimeError("boom")):
        with pytest.raises(SystemExit) as exc:
            merge_sources.main(["--partition", "2026-06"])
        assert exc.value.code != 0


def test_merge_sources_rejects_invalid_month_format():
    with patch("merge_sources.run_merge") as mock_merge:
        with pytest.raises(SystemExit) as exc:
            merge_sources.main(["--partition", "2026-13"])
        assert exc.value.code == 2
    mock_merge.assert_not_called()


def test_merge_sources_requires_partition():
    with patch("merge_sources.run_merge") as mock_merge:
        with pytest.raises(SystemExit) as exc:
            merge_sources.main([])
        assert exc.value.code == 2
    mock_merge.assert_not_called()
