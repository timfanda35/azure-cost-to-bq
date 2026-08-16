import argparse
import logging
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from src.log import configure_logging
from src.merge_pipeline import run_merge

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
            "Merge two Azure Cost Management export sources (SOURCE_A_*/SOURCE_B_* env "
            "vars) into one BigQuery billing-period partition. For migrating an EA export "
            "mid-month, where the cutover doesn't land on a billing-period boundary."
        )
    )
    parser.add_argument("--partition", required=True, type=_parse_month, metavar="YYYY-MM",
                        help="Billing period to merge.")
    parser.add_argument("--label", default=None,
                        help="GCS staging folder / export_name label for this merge "
                             "(default: derived from both sources' export names).")
    args = parser.parse_args(argv)

    logger.info("merge.started", extra={
        "log_event": "merge.started",
        "partition": args.partition,
        "label": args.label,
    })

    try:
        result = run_merge(args.partition, label=args.label)
    except Exception as exc:
        logger.error("merge.failed", extra={
            "log_event": "merge.failed",
            "partition": args.partition,
            "error": str(exc),
        }, exc_info=True)
        sys.exit(1)

    if result.get("skipped"):
        logger.warning("merge.skipped", extra={
            "log_event": "merge.skipped",
            "partition": args.partition,
            "reason": result.get("reason"),
            "source": result.get("source"),
        })
        sys.exit(1)

    logger.info("merge.complete", extra={
        "log_event": "merge.complete",
        "partition": args.partition,
        "run_id": result.get("run_id"),
        "files": result.get("files"),
        "bq_table": result.get("bq_table"),
    })
    sys.exit(0)


if __name__ == "__main__":
    main()
