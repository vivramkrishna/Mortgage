"""Static mock data and business logic for the Lloyds Banking Demo Plugin.

All data here is synthetic and used purely for demonstration purposes.
"""
from __future__ import annotations

from datetime import datetime, timedelta

MOCK_TRANSACTIONS: list[dict] = [
    {
        "id": "txn_001",
        "merchant": "Starbucks",
        "category": "Coffee & Dining",
        "amount": 12.50,
        "date": (datetime(2026, 7, 30, 8, 15)).isoformat(),
        "icon": "☕",
        "status": "completed",
    },
    {
        "id": "txn_002",
        "merchant": "Amazon",
        "category": "Shopping",
        "amount": 89.99,
        "date": (datetime(2026, 7, 29, 19, 42)).isoformat(),
        "icon": "📦",
        "status": "completed",
    },
    {
        "id": "txn_003",
        "merchant": "Tesco",
        "category": "Groceries",
        "amount": 42.10,
        "date": (datetime(2026, 7, 29, 12, 5)).isoformat(),
        "icon": "🛒",
        "status": "completed",
    },
    {
        "id": "txn_004",
        "merchant": "Uber",
        "category": "Transport",
        "amount": 18.25,
        "date": (datetime(2026, 7, 28, 22, 10)).isoformat(),
        "icon": "🚕",
        "status": "completed",
    },
    {
        "id": "txn_005",
        "merchant": "Netflix",
        "category": "Entertainment",
        "amount": 10.99,
        "date": (datetime(2026, 7, 27, 0, 1)).isoformat(),
        "icon": "🎬",
        "status": "completed",
    },
]


def get_transactions() -> list[dict]:
    return sorted(MOCK_TRANSACTIONS, key=lambda t: t["date"], reverse=True)


def get_transaction_summary() -> dict:
    txns = get_transactions()
    total = round(sum(t["amount"] for t in txns), 2)
    latest = txns[0] if txns else None
    by_category: dict[str, float] = {}
    for t in txns:
        by_category[t["category"]] = round(by_category.get(t["category"], 0) + t["amount"], 2)

    return {
        "total_spending": total,
        "transaction_count": len(txns),
        "latest_transaction": latest,
        "spending_by_category": by_category,
        "average_transaction": round(total / len(txns), 2) if txns else 0,
        "transactions": txns,
    }


# --- Mortgage business logic -------------------------------------------------

MORTGAGE_INTEREST_RATE = 4.2  # percent, fixed rate used for this demo
MORTGAGE_TERM_YEARS = 25
LOAN_TO_INCOME_MULTIPLE = 5.0  # simplistic affordability multiple used for the demo


def calculate_mortgage_offer(
    annual_salary: float,
    property_value: float | None = None,
    deposit_amount: float | None = None,
    loan_amount: float | None = None,
    preferred_term: int | None = None,
    application_id: str | None = None,
) -> dict:
    """Simplistic mock mortgage affordability + repayment calculation.

    With only `annual_salary` given, this mirrors the original spec example:
    a £50,000 salary yields a £250,000 loan (5x salary), 4.2% rate, 25-year
    term, ~£1,350/month — this is the path the legacy `/api/mortgage/estimate`
    REST endpoint still uses.

    When the fuller field set (from the chat mortgage journey) is supplied,
    the offered loan is capped by both income and property/deposit headroom.
    """
    if annual_salary <= 0:
        raise ValueError("annual_salary must be a positive number")

    max_by_income = round(annual_salary * LOAN_TO_INCOME_MULTIPLE, 2)
    requested_loan_amount = loan_amount if loan_amount is not None else max_by_income

    cap_reason = None
    if property_value is not None:
        max_by_property = max(round(property_value - (deposit_amount or 0), 2), 0)
        offered_loan_amount = min(requested_loan_amount, max_by_income, max_by_property)
        if offered_loan_amount < requested_loan_amount:
            cap_reason = (
                "income affordability"
                if max_by_income < max_by_property
                else "property value less deposit"
            )
    else:
        max_by_property = None
        offered_loan_amount = min(requested_loan_amount, max_by_income)
        if offered_loan_amount < requested_loan_amount:
            cap_reason = "income affordability"

    term_years = preferred_term or MORTGAGE_TERM_YEARS
    term_years = max(5, min(int(term_years), 35))

    monthly_rate = (MORTGAGE_INTEREST_RATE / 100) / 12
    n_payments = term_years * 12
    if monthly_rate == 0:
        monthly_payment = offered_loan_amount / n_payments
    else:
        monthly_payment = (
            offered_loan_amount
            * (monthly_rate * (1 + monthly_rate) ** n_payments)
            / ((1 + monthly_rate) ** n_payments - 1)
        )

    total_repayable = round(monthly_payment * n_payments, 2)
    total_interest = round(total_repayable - offered_loan_amount, 2)

    eligible = cap_reason is None
    notes = (
        "Estimate based on a standard 5x income affordability multiple. "
        "Final offer subject to full underwriting and credit checks."
        if eligible
        else f"Your requested loan amount was reduced due to {cap_reason}. "
        "Final offer subject to full underwriting and credit checks."
    )

    return {
        "annual_salary": annual_salary,
        "property_value": property_value,
        "deposit_amount": deposit_amount,
        "requested_loan_amount": requested_loan_amount,
        "loan_amount": round(offered_loan_amount, 2),
        "interest_rate": MORTGAGE_INTEREST_RATE,
        "term_years": term_years,
        "estimated_monthly_payment": round(monthly_payment, 2),
        "total_repayable": total_repayable,
        "total_interest": total_interest,
        "application_status": "Initiated",
        "application_id": application_id or f"MORT-{int(annual_salary):06d}",
        "eligibility": {
            "eligible": eligible,
            "loan_to_income_multiple": LOAN_TO_INCOME_MULTIPLE,
            "notes": notes,
        },
        "next_steps": [
            "Confirm your identity and proof of income",
            "Submit a property valuation request",
            "Speak with a Lloyds mortgage advisor",
            "Receive your formal Decision in Principle",
        ],
        "generated_at": datetime.now().isoformat(),
    }
