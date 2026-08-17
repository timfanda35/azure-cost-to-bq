import argparse
import logging
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from google.cloud import bigquery

from src.log import configure_logging
from src.pipeline import run_pipeline

configure_logging()
logger = logging.getLogger(__name__)


def _parse_month(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a valid YYYY-MM billing period")
    return value


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Load this job's configured export into an existing BQ billing-period "
            "partition without truncating it (WRITE_APPEND). For landing a second "
            "source into a partition another job already loaded, e.g. backfill.py for "
            "source A followed by append.py (pointed at source B's env vars) for the "
            "same partition."
        )
    )
    parser.add_argument("--partition", required=True, type=_parse_month, metavar="YYYY-MM",
                        help="Billing period to append to.")
    args = parser.parse_args(argv)

    logger.info("append.started", extra={
        "log_event": "append.started",
        "partition": args.partition,
    })

    try:
        result = run_pipeline(
            partition=args.partition,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        periods_loaded = result.get("periods_loaded") or 0
        periods_skipped = result.get("periods_skipped") or 0
        if periods_loaded == 0 and periods_skipped > 0:
            period_results = result.get("results") or [{}]
            logger.warning("append.skipped", extra={
                "log_event": "append.skipped",
                "run_id": result.get("run_id"),
                "partition": args.partition,
                "reason": period_results[0].get("reason"),
            })
            sys.exit(1)
        logger.info("append.complete", extra={
            "log_event": "append.complete",
            "run_id": result.get("run_id"),
            "partition": args.partition,
            "periods_loaded": result.get("periods_loaded"),
            "periods_skipped": result.get("periods_skipped"),
        })
        sys.exit(0)
    except Exception as exc:
        logger.error("append.failed", extra={
            "log_event": "append.failed",
            "partition": args.partition,
            "error": str(exc),
        }, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
