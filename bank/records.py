"""In-memory session records backing the Command Centre.

One record per mortgage application the bank has seen. Holds the live chat
feed mirrored from the MCP backend, the underwriting output, the offer, and
the goal-plan progress. Demo-only: not persisted, cleared on restart.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# The bank's fixed execution plan for a mortgage application. Steps move
# pending -> done as the journey progresses, which is what the dashboard's
# Goal Plan panel renders.
GOAL_STEPS = [
    "GATHER_DATA",
    "RUN_CHECKS",
    "CALCULATE_OFFER",
    "PRESENT_OFFER",
    "NEGOTIATE",
]

GOAL_DESCRIPTION = (
    "Conduct a thorough risk assessment, compute a risk-adjusted profitable "
    "mortgage offer, and finalise terms that balance bank profitability with "
    "customer affordability."
)

_records: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_or_create(session_id: str) -> dict[str, Any]:
    record = _records.get(session_id)
    if record is None:
        record = {
            "session_id": session_id,
            "status": "In Progress",
            "customer": None,
            "chat": [],
            "checks": {},
            "decision": None,
            "offer": None,
            "strengths": [],
            "concerns": [],
            "rationale": None,
            "failed_checks": [],
            "total_risk_points": 0,
            "negotiation_log": [],
            "goal": {
                "description": GOAL_DESCRIPTION,
                "status": "IN_PROGRESS",
                "steps": [{"name": name, "state": "pending"} for name in GOAL_STEPS],
            },
            "created_at": _now(),
            "updated_at": _now(),
        }
        _records[session_id] = record
    return record


def append_chat(session_id: str, sender: str, text: str) -> dict[str, Any]:
    record = get_or_create(session_id)
    record["chat"].append({"sender": sender, "text": text, "at": _now()})
    record["updated_at"] = _now()
    return record


def complete_steps(record: dict[str, Any], *names: str) -> None:
    for step in record["goal"]["steps"]:
        if step["name"] in names:
            step["state"] = "done"


def record_underwriting(
    session_id: str,
    customer: dict[str, Any],
    application: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    record = get_or_create(session_id)

    record["customer"] = {
        "customer_id": customer.get("customer_id"),
        "full_name": customer.get("full_name"),
        "email": customer.get("email"),
        "applicant_annual_income": customer.get("applicant_annual_income"),
        "credit_score": customer.get("credit_score"),
        "employment_type": customer.get("employment_type"),
        "employer_name": customer.get("employer_name"),
        "existing_debts": customer.get("existing_debts"),
        "deposit_amount": customer.get("deposit_amount"),
        # The property the customer is buying wins over whatever the bank had
        # on file — it is what the underwriting was actually measured against.
        "property_value": application.get("property_value", customer.get("property_value")),
    }
    record["application"] = application
    record["checks"] = result["checks"]
    record["failed_checks"] = result["failed_checks"]
    record["total_risk_points"] = result["total_risk_points"]
    record["offer"] = result["offer"]
    record["strengths"] = result["strengths"]
    record["concerns"] = result["concerns"]
    record["rationale"] = result["rationale"]
    record["decision"] = {
        "approved": result["approved"],
        "reason": result["decision_reason"],
    }

    if result["approved"]:
        record["status"] = "Offer Made"
        complete_steps(record, "GATHER_DATA", "RUN_CHECKS", "CALCULATE_OFFER", "PRESENT_OFFER")
        record["goal"]["status"] = "COMPLETED"
    else:
        record["status"] = "Declined"
        complete_steps(record, "GATHER_DATA", "RUN_CHECKS")
        record["goal"]["status"] = "FAILED"

    record["updated_at"] = _now()
    return record


def all_records() -> dict[str, dict[str, Any]]:
    return _records


def get(session_id: str) -> dict[str, Any] | None:
    return _records.get(session_id)
