"""Logging setup shared by the API, the MQTT bridge, the scheduler and the CLIs."""

from __future__ import annotations

import logging
import sys

from app.core.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging() -> None:
    """Install a single stdout handler and quieten noisy third-party loggers."""
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    # Replace handlers rather than appending, otherwise uvicorn's reloader ends
    # up duplicating every log line on each restart.
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(handler)

    for noisy in ("sqlalchemy.engine.Engine", "amqtt", "transitions", "httpx", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
