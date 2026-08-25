"""HTTP client for the bank core service.

The MCP backend is the ChatGPT-facing surface; the bank is a separate process
that owns customer records and underwriting. Every call here is deliberately
forgiving — if the bank is unreachable the tools degrade to a clear message
rather than dropping a traceback into the conversation.
"""
from __future__ import annotations

import logging

import httpx

from backend import config

logger = logging.getLogger(__name__)

TIMEOUT = 8.0


class BankUnavailable(RuntimeError):
    """The bank core could not be reached."""


def _url(path: str) -> str:
    return f"{config.BANK_BASE_URL.rstrip('/')}{path}"


def get_customer(email: str) -> dict | None:
    """Resolve a customer by email. Returns None when the bank has no record
    for that address (a 404), which is a normal outcome, not an error."""
    try:
        response = httpx.get(_url("/api/customer"), params={"email": email}, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("Bank customer lookup failed for %s: %s", email, exc)
        raise BankUnavailable(
            "The bank service is not responding. Make sure it is running "
            f"at {config.BANK_BASE_URL}."
        ) from exc

    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def underwrite(
    session_id: str,
    email: str,
    loan_amount: float,
    property_value: float,
    preferred_term: int,
) -> dict:
    """Run the bank's underwriting for this application."""
    try:
        response = httpx.post(
            _url("/api/underwrite"),
            json={
                "session_id": session_id,
                "email": email,
                "loan_amount": loan_amount,
                "property_value": property_value,
                "preferred_term": preferred_term,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("Bank underwriting failed for %s: %s", session_id, exc)
        raise BankUnavailable(
            "The bank service is not responding, so this application could not "
            f"be underwritten. Make sure it is running at {config.BANK_BASE_URL}."
        ) from exc

    response.raise_for_status()
    return response.json()


def check_affordability(
    combined_annual_income: float,
    deposit_percentage: float | None = None,
    deposit_amount: float | None = None,
    term_years: int | None = None,
) -> dict:
    """Indicative "what could I borrow" quote. No session/customer identity
    involved, so unlike `underwrite()` this needs no session_id or email.
    Pass exactly one of deposit_percentage/deposit_amount — whichever the
    customer answered with."""
    try:
        response = httpx.post(
            _url("/api/affordability"),
            json={
                "combined_annual_income": combined_annual_income,
                "deposit_percentage": deposit_percentage,
                "deposit_amount": deposit_amount,
                "term_years": term_years,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("Bank affordability check failed: %s", exc)
        raise BankUnavailable(
            "The bank service is not responding, so this affordability check "
            f"could not run. Make sure it is running at {config.BANK_BASE_URL}."
        ) from exc

    response.raise_for_status()
    return response.json()


def reset_records() -> None:
    """Ask the bank to wipe its session records (debug-gated on the bank side).

    Best-effort: reached only from the backend's own debug reset, and a bank
    that is down or predates the endpoint must not break the reset.
    """
    try:
        httpx.post(_url("/api/_debug/reset"), timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        logger.debug("Could not reset bank records: %s", exc)


def log_chat(session_id: str, sender: str, text: str) -> None:
    """Mirror a message into the bank's live feed.

    Best-effort: the customer journey must never fail because the bank's
    dashboard is down, so failures here are swallowed.
    """
    try:
        httpx.post(
            _url(f"/api/session/{session_id}/chat"),
            json={"sender": sender, "text": text},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.debug("Could not mirror chat to bank for %s: %s", session_id, exc)
