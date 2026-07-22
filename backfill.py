import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from src.log import configure_logging
from src.pipeline import run_pipeline

configure_logging()
logger = logging.getLogger(__name__)


def _parse_month(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a valid YYYY-MM billing period")


def month_range(start: datetime, end: datetime) -> list[str]:
    """Inclusive list of "YYYY-MM" strings from start to end, oldest first."""
    periods = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return periods


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Backfill the Azure Cost to BQ sync job across a range of billing periods."
    )
    parser.add_argument("--start", required=True, type=_parse_month, metavar="YYYY-MM",
                        help="First billing period to process (inclusive).")
    parser.add_argument("--end", required=True, type=_parse_month, metavar="YYYY-MM",
                        help="Last billing period to process (inclusive).")
    args = parser.parse_args(argv)

    if args.start > args.end:
        parser.error(f"--start ({args.start:%Y-%m}) must not be after --end ({args.end:%Y-%m})")

    periods = month_range(args.start, args.end)

    now = datetime.now(timezone.utc)
    run_id = f"backfill-{now.strftime('%Y%m%d')}-{int(now.timestamp())}"

    logger.info("backfill.started", extra={
        "log_event": "backfill.started",
        "run_id": run_id,
        "periods": periods,
    })

    start_time = time.monotonic()
    months_succeeded = 0
    months_skipped = 0
    months_failed = 0

    for partition in periods:
        try:
            result = run_pipeline(partition=partition)
        except Exception as exc:
            months_failed += 1
            logger.error("backfill.month.failed", extra={
                "log_event": "backfill.month.failed",
                "run_id": run_id,
                "partition": partition,
                "error": str(exc),
            }, exc_info=True)
            continue

        periods_loaded = result.get("periods_loaded") or 0
        periods_skipped = result.get("periods_skipped") or 0
        if periods_loaded == 0 and periods_skipped > 0:
            months_skipped += 1
            period_results = result.get("results") or [{}]
            logger.warning("backfill.month.skipped", extra={
                "log_event": "backfill.month.skipped",
                "run_id": run_id,
                "partition": partition,
                "reason": period_results[0].get("reason"),
            })
        else:
            months_succeeded += 1
            logger.info("backfill.month.complete", extra={
                "log_event": "backfill.month.complete",
                "run_id": run_id,
                "partition": partition,
                "pipeline_run_id": result.get("run_id"),
                "periods_loaded": result.get("periods_loaded"),
                "periods_skipped": result.get("periods_skipped"),
            })

    duration = time.monotonic() - start_time
    logger.info("backfill.complete", extra={
        "log_event": "backfill.complete",
        "run_id": run_id,
        "months_succeeded": months_succeeded,
        "months_skipped": months_skipped,
        "months_failed": months_failed,
        "duration_seconds": round(duration, 2),
    })

    sys.exit(0)


if __name__ == "__main__":
    main()
