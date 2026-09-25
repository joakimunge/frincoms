"""Bounded, local diagnostic log for the desktop client."""

import logging
from logging.handlers import RotatingFileHandler

from settings import settings_path


def log_path():
    return settings_path().parent / "frincoms.log"


def setup_logging(path=None):
    path = path or log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("frincoms")
    if not logger.handlers:
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
