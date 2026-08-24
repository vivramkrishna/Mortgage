"""Request/response audit logging for every MCP tool call.

Every call is logged as its own blank-line-framed block — a blank line,
the request, the response, a blank line — so entries are easy to pick out
by eye when tailing the log, and easy to split programmatically on the
blank-line boundary.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as types

logger = logging.getLogger("backend.requests")


def _result_to_jsonable(result: Any) -> Any:
    if isinstance(result, types.CallToolResult):
        return {
            "isError": result.isError,
            "content": [
                {"type": block.type, "text": getattr(block, "text", None)}
                for block in (result.content or [])
            ],
            "structuredContent": result.structuredContent,
        }
    return result


def log_request(tool_name: str, arguments: dict[str, Any]) -> None:
    logger.info("")
    logger.info("REQUEST  | tool=%s | arguments=%s", tool_name, json.dumps(arguments, default=str))


def log_response(tool_name: str, result: Any) -> None:
    payload = _result_to_jsonable(result)
    logger.info("RESPONSE | tool=%s | %s", tool_name, json.dumps(payload, default=str))
    logger.info("")
