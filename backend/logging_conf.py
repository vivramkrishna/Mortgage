"""Logging configuration for the Lloyds Banking Demo Plugin backend."""
import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. re-import under uvicorn --reload)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.setLevel(level)
    root.addHandler(handler)

    # Quiet down noisy third-party loggers while keeping our own verbose.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
