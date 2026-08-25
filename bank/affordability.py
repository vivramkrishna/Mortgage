"""Indicative mortgage affordability estimator.

Deliberately independent of `underwriting.py`: this runs before any
application exists, needs no customer record, credit score or property, and
produces a ballpark figure the customer can act on — not an offer. The only
piece borrowed from underwriting is the amortization formula, since a
monthly payment is computed the same way regardless of product.

Kept free of LTV/property coupling so a future `PersonalLoanAgent` (or any
other product needing "how much could I borrow against my income") can call
`max_loan_for_income` directly without dragging in mortgage-specific
concepts like property value.
"""
from __future__ import annotations

from typing import Any

from bank import product_config
from bank.underwriting import calc_monthly_payment


def max_loan_for_income(
    combined_annual_income: float,
    income_multiple: float = product_config.AFFORDABILITY_INCOME_MULTIPLE,
) -> float:
    """Product-agnostic affordability cap — no property/LTV involved."""
    return round(max(0.0, combined_annual_income) * income_multiple, 2)


def calculate_affordability(
    combined_annual_income: float,
    deposit_percentage: float | None = None,
    deposit_amount: float | None = None,
    term_years: int | None = None,
) -> dict[str, Any]:
    """Indicative mortgage affordability quote.

    Callers naturally answer the deposit question either way — "15% saved"
    or "£15,000 saved" — so this accepts exactly one of `deposit_percentage`
    (e.g. 15 for 15%, not a ratio) or `deposit_amount` (cash, GBP) and
    derives the other. Raises ValueError on inputs that can't produce a sane
    quote.
    """
    if combined_annual_income <= 0:
        raise ValueError("combined_annual_income must be greater than 0")
    if deposit_percentage is None and deposit_amount is None:
        raise ValueError("either deposit_percentage or deposit_amount is required")
    if deposit_percentage is not None and deposit_amount is not None:
        raise ValueError("provide only one of deposit_percentage or deposit_amount")

    term_years = term_years or product_config.DEFAULT_TERM_YEARS
    loan_amount = max_loan_for_income(combined_annual_income)

    if deposit_percentage is not None:
        if not (0 <= deposit_percentage < 100):
            raise ValueError("deposit_percentage must be between 0 and 100 (exclusive of 100)")
        property_price = round(loan_amount / (1 - deposit_percentage / 100), 2)
        resolved_deposit_amount = round(property_price - loan_amount, 2)
        resolved_deposit_percentage = deposit_percentage
    else:
        if deposit_amount < 0:
            raise ValueError("deposit_amount cannot be negative")
        property_price = round(loan_amount + deposit_amount, 2)
        resolved_deposit_amount = deposit_amount
        resolved_deposit_percentage = round((deposit_amount / property_price) * 100, 2) if property_price else 0.0

    interest_rate = product_config.INDICATIVE_RATE_5YR_FIXED
    monthly_payment = calc_monthly_payment(loan_amount, term_years, interest_rate)
    total_amount_repaid = round(monthly_payment * term_years * 12, 2)
    total_interest = round(total_amount_repaid - loan_amount, 2)

    return {
        "property_price": property_price,
        "loan_amount": loan_amount,
        "deposit_amount": resolved_deposit_amount,
        "deposit_percentage": resolved_deposit_percentage,
        "interest_rate": interest_rate,
        "term_years": term_years,
        "monthly_payment": monthly_payment,
        "total_interest": total_interest,
        "total_amount_repaid": total_amount_repaid,
        # Explicitly not an ACCEPT_OFFER-eligible object — no underwriting,
        # no credit check, no binding terms.
        "is_indicative": True,
    }
