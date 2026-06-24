import logging
import os

from pythonjsonlogger import jsonlogger


def configure_logging() -> None:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.root.handlers.clear()
    logging.root.addHandler(handler)
    logging.root.setLevel(level)
