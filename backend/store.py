"""Tiny in-memory store bridging chat-originated mortgage applications to the
Lloyds web app. Demo-only: not persisted, not thread-safe beyond the GIL, and
reset whenever the backend restarts.
"""
from __future__ import annotations

import uuid

_applications: dict[str, dict] = {}
_latest_application_id: str | None = None

# In-progress mortgage applications being collected field-by-field in chat,
# before they've been finalized into `_applications`.
_drafts: dict[str, dict] = {}
_latest_draft_id: str | None = None


def save_application(offer: dict) -> None:
    global _latest_application_id
    _applications[offer["application_id"]] = offer
    _latest_application_id = offer["application_id"]


def get_application(application_id: str) -> dict | None:
    return _applications.get(application_id)


def get_latest_application() -> dict | None:
    if _latest_application_id is None:
        return None
    return _applications.get(_latest_application_id)


def create_draft() -> dict:
    global _latest_draft_id
    application_id = f"MORT-{uuid.uuid4().hex[:8].upper()}"
    draft = {"application_id": application_id, "status": "collecting_info", "fields": {}}
    _drafts[application_id] = draft
    _latest_draft_id = application_id
    return draft


def get_draft(application_id: str) -> dict | None:
    return _drafts.get(application_id)


def get_latest_draft() -> dict | None:
    if _latest_draft_id is None:
        return None
    return _drafts.get(_latest_draft_id)


def update_draft(application_id: str, **fields) -> dict | None:
    draft = _drafts.get(application_id)
    if draft is None:
        return None
    for key, value in fields.items():
        if value is not None:
            draft["fields"][key] = value
    return draft


def update_draft_remove(application_id: str, field: str) -> None:
    """Drop a single collected field so it gets asked again."""
    draft = _drafts.get(application_id)
    if draft is not None:
        draft["fields"].pop(field, None)


def discard_draft(application_id: str) -> None:
    _drafts.pop(application_id, None)


def reset() -> None:
    """Clear all applications and drafts — local-dev-only, used by
    scripts/test_mcp.py to get a deterministic starting state."""
    global _latest_application_id, _latest_draft_id
    _applications.clear()
    _drafts.clear()
    _latest_application_id = None
    _latest_draft_id = None
