"""Process-global OTP authentication session for the demo backend.

Mirrors backend/store.py's "one active thing at a time" convention: this is a
single-user, in-memory demo, so authentication state lives in a single
module-level session rather than per-user session infrastructure. It is
shared by the mortgage and transactions flows so that verifying once is
reused for the rest of the chat session.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta
from typing import Literal

from backend import email_client

Purpose = Literal["mortgage", "transactions"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

OTP_TTL = timedelta(minutes=5)
SESSION_TTL = timedelta(minutes=60)

_session: dict | None = None

# Mortgage applications whose OWN OTP has been confirmed. Deliberately
# per-application rather than a single global flag — a verification done for
# one draft (or a different chat/tab) must never auto-approve an unrelated,
# freshly-started draft. Only the transactions flow (which has no per-item
# identity to key off) uses the simpler global "verified within this window"
# reuse via is_authenticated().
_verified_application_ids: set[str] = set()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def is_authenticated() -> bool:
    if not _session or not _session.get("verified"):
        return False
    verified_at = _session.get("verified_at")
    return bool(verified_at) and datetime.now() - verified_at < SESSION_TTL


def is_application_verified(application_id: str | None) -> bool:
    return bool(application_id) and application_id in _verified_application_ids


def verified_email() -> str | None:
    """The email address of the currently verified session, if any.

    This is what identifies the customer to the bank, so it is only returned
    once the code for that address has actually been confirmed.
    """
    if not _session or not _session.get("verified"):
        return None
    return _session.get("email")


def start_verification(email: str, purpose: Purpose, application_id: str | None = None) -> None:
    global _session

    if not is_valid_email(email):
        raise ValueError("That doesn't look like a valid email address.")

    otp = f"{secrets.randbelow(1_000_000):06d}"
    _session = {
        "email": email.strip(),
        "otp": otp,
        "otp_expires_at": datetime.now() + OTP_TTL,
        "verified": False,
        "verified_at": None,
        "pending_purpose": purpose,
        "pending_application_id": application_id,
    }

    email_client.send_otp_email(email.strip(), otp)


def verify(otp: str) -> bool:
    if not _session:
        return False

    if datetime.now() > _session["otp_expires_at"]:
        return False

    if (otp or "").strip() != _session["otp"]:
        return False

    _session["verified"] = True
    _session["verified_at"] = datetime.now()
    if _session.get("pending_purpose") == "mortgage" and _session.get("pending_application_id"):
        _verified_application_ids.add(_session["pending_application_id"])
    return True


def discard_application_verification(application_id: str) -> None:
    _verified_application_ids.discard(application_id)


def cancel_pending() -> tuple[Purpose | None, str | None]:
    """Abandon the in-flight verification and return what it was for.

    Used when a code is entered incorrectly: the session is torn down entirely
    so the issued code can't be retried or reused, and the caller can clean up
    whatever flow was waiting on it.
    """
    global _session
    if not _session:
        return None, None
    purpose = _session.get("pending_purpose")
    application_id = _session.get("pending_application_id")
    _session = None
    return purpose, application_id


def pop_pending() -> tuple[Purpose | None, str | None]:
    """Consume and return the (purpose, application_id) that triggered the
    most recent successful verification."""
    if not _session:
        return None, None
    purpose = _session.get("pending_purpose")
    application_id = _session.get("pending_application_id")
    _session["pending_purpose"] = None
    _session["pending_application_id"] = None
    return purpose, application_id


def reset() -> None:
    """Clear all verification state.

    Local-dev-only, so `scripts/test_mcp.py` can assert against a known
    unauthenticated starting point on every run — see
    backend.config.DEBUG_EXPOSE_OTP.
    """
    global _session
    _session = None
    _verified_application_ids.clear()


def peek_otp() -> str | None:
    """Local-dev-only accessor for the currently pending OTP — see
    backend.config.DEBUG_EXPOSE_OTP."""
    return _session["otp"] if _session else None


def peek_state() -> dict | None:
    """Local-dev-only accessor for the full pending session (which email is
    about to receive/receive the OTP, and for what purpose) — see
    backend.config.DEBUG_EXPOSE_OTP."""
    if not _session:
        return None
    return {
        "email": _session.get("email"),
        "otp": _session.get("otp"),
        "purpose": _session.get("pending_purpose"),
        "application_id": _session.get("pending_application_id"),
        "verified": _session.get("verified"),
    }
