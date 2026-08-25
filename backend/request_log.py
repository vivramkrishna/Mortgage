"""Audit logging for every MCP request/response the server dispatches.

Covers tools/list, tools/call, resources/list, resources/read, ping — every
type registered in `Server.request_handlers` (see the wrapping applied in
backend/mcp_server.py) — not just tool calls. Each cycle is logged as one
blank-line-framed, pretty-printed block so entries are easy to pick out by
eye when tailing the log and easy to split programmatically on the blank
line boundary.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as types
from pydantic import BaseModel

logger = logging.getLogger("backend.requests")

_DIVIDER = "-" * 100


def _jsonable(obj: Any) -> Any:
    """Best-effort conversion of MCP/pydantic objects into plain JSON-able data."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _pretty(obj: Any) -> str:
    return json.dumps(_jsonable(obj), indent=2, default=str, ensure_ascii=False)


def log_call(request: Any, response: Any, *, error: BaseException | None = None) -> None:
    """Log one full MCP request -> response (or exception) cycle, pretty-printed."""
    method = getattr(request, "method", type(request).__name__)

    header = f"MCP CALL | method={method}"
    if isinstance(request, types.CallToolRequest):
        header += f" | tool_call={request.params.name}"

    logger.info("")
    logger.info(_DIVIDER)
    logger.info(header)
    logger.info(_DIVIDER)
    logger.info("REQUEST:\n%s", _pretty(request))
    logger.info("")
    if error is not None:
        logger.info("EXCEPTION:\n%s: %s", type(error).__name__, error)
    else:
        logger.info("RESPONSE:\n%s", _pretty(response))
    logger.info(_DIVIDER)
    logger.info("")
