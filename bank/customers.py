"""The bank's customer book.

Three synthetic records, keyed by email address. The email a customer gives
during verification is what identifies them here, so the underwriting engine
reads their real income, credit score and property position rather than asking
for it in chat.

The three are spread deliberately across risk bands — strong, middling and
weak — so the same journey produces visibly different decisions and pricing.
All data is synthetic.
"""
from __future__ import annotations

from typing import Any

CUSTOMERS: dict[str, dict[str, Any]] = {
    "konavivekramakrishna@gmail.com": {
        "customer_id": "CUST00001",
        "full_name": "Vivek Rama Krishna Kona",
        "email": "konavivekramakrishna@gmail.com",
        "applicant_annual_income": 95_000.0,
        "other_source_of_income": 6_000.0,
        "credit_score": 781,
        "employment_type": "employed",
        "employer_name": "Lloyds Technology Centre",
        "date_of_birth": "1994-04-12",
        "existing_debts": 4_800.0,
        "monthly_outgoings": 1_150.0,
        "property_value": 520_000.0,
        "total_property_value": 520_000.0,
        "deposit_amount": 260_000.0,
    },
    "notimportantupdatesonly@gmail.com": {
        "customer_id": "CUST00002",
        "full_name": "Alex Whitfield",
        "email": "notimportantupdatesonly@gmail.com",
        "applicant_annual_income": 62_000.0,
        "other_source_of_income": 0.0,
        "credit_score": 712,
        "employment_type": "self-employed",
        "employer_name": "Whitfield Design Studio",
        "date_of_birth": "1988-09-30",
        "existing_debts": 18_500.0,
        "monthly_outgoings": 1_640.0,
        "property_value": 340_000.0,
        "total_property_value": 340_000.0,
        "deposit_amount": 68_000.0,
    },
    "sankranthibhuvaneswar@gmail.com": {
        "customer_id": "CUST00003",
        "full_name": "Bhuvaneswar Sankranthi",
        "email": "sankranthibhuvaneswar@gmail.com",
        "applicant_annual_income": 44_000.0,
        "other_source_of_income": 2_400.0,
        "credit_score": 648,
        "employment_type": "contractor",
        "employer_name": "Northgate Logistics",
        "date_of_birth": "1979-01-22",
        "existing_debts": 31_000.0,
        "monthly_outgoings": 1_480.0,
        "property_value": 275_000.0,
        "total_property_value": 275_000.0,
        "deposit_amount": 22_000.0,
    },
}


def get_customer(email: str) -> dict[str, Any] | None:
    """Look up a customer by email. Case- and whitespace-insensitive."""
    return CUSTOMERS.get((email or "").strip().lower())


def list_customers() -> list[dict[str, Any]]:
    return list(CUSTOMERS.values())
