import argparse
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from src.log import configure_logging
from src.pipeline import run_pipeline

configure_logging()
logger = logging.getLogger(__name__)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run the Azure Cost to BQ sync job.")
    parser.add_argument("--partition", default=None, metavar="YYYY-MM",
                        help="Billing period to process. Overrides the PARTITION env var.")
    parser.add_argument("--report", default=None, choices=["actual", "amortized", "focus"],
                        help="Limit to one report type. Overrides the REPORT env var.")
    args = parser.parse_args(argv)

    partition = args.partition or os.environ.get("PARTITION") or None
    report = args.report or os.environ.get("REPORT") or None

    logger.info("job.started", extra={
        "log_event": "job.started",
        "partition": partition,
        "report": report,
    })

    try:
        result = run_pipeline(partition=partition, report=report)
        logger.info("job.complete", extra={
            "log_event": "job.complete",
            "run_id": result.get("run_id"),
            "periods_loaded": result.get("periods_loaded"),
            "periods_skipped": result.get("periods_skipped"),
        })
        sys.exit(0)
    except Exception as exc:
        logger.error("job.failed", extra={
            "log_event": "job.failed",
            "error": str(exc),
        }, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
