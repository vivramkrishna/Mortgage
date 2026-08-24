"""Logging configuration for the Lloyds Banking Demo Plugin backend."""
import logging
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
REQUESTS_LOG_FILE = LOG_DIR / "mcp_requests.log"


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

    # Every MCP tool request/response (backend/request_log.py) also goes to
    # its own file, blank-line-framed per call, so a query can be audited
    # without digging through the general server log.
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(REQUESTS_LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(fmt="%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    requests_logger = logging.getLogger("backend.requests")
    requests_logger.addHandler(file_handler)
    requests_logger.propagate = True  # still shows on stdout via the root handler
