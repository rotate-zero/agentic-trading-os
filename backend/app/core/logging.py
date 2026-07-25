"""
Logging setup, called once from main.py's app startup. Kept deliberately
simple for Phase 2 — structured/JSON logging is a later concern, not a
blocker for getting the skeleton running.
"""
import logging
import sys


def configure_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Quiet down noisy third-party loggers at INFO by default.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING if not debug else logging.INFO)
