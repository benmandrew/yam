"""Application logging setup.

A single entry point (`configure_logging`) so the app, worker, and any script
share one consistent, timestamped log format. Level is taken from the `LOG_LEVEL`
environment variable (default INFO). Idempotent — safe to call more than once.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT)
    # Quiet yt-dlp's own chatter; the worker surfaces failures on the job row.
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)
    _CONFIGURED = True
