"""Mortgage underwriting engine.

Ported from the ACP mortgage sub-agent's skill classes into plain functions —
this service has no ACP protocol, message bus or LLM client, and the checks
never needed any of that. Each check returns a `passed` flag, a human-readable
`reason` and, where the source graded risk, `risk_points`, which is what the
Command Centre renders per row.

Two defects in the original were fixed on the way in; both are marked BUGFIX
below.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

# --- Policy constants --------------------------------------------------------

MIN_CREDIT_SCORE = 580
MAX_DTI_PERCENT = 43.0
MAX_LTV_PERCENT = 95.0
MIN_LOAN = 50_000.0
MAX_LOAN = 2_000_000.0
MIN_TERM_YEARS = 5
MAX_TERM_YEARS = 35
INCOME_MULTIPLE = 4.5
RATE_FLOOR = 3.49

# Failing one of these stops the application outright. Everything else loads
# the rate instead of rejecting — same split as the source.
HARD_FAIL_CHECKS = {"validate", "age", "term"}

# Order the dashboard renders them in.
CHECK_LABELS = {
    "validate": "Field Validation",
    "credit": "Credit Score",
    "dti": "Debt-to-Income (DTI)",
    "ltv": "Loan-to-Value (LTV)",
    "age": "Age Eligibility",
    "loan": "Loan Amount Limits",
    "term": "Mortgage Term",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


# --- Individual checks -------------------------------------------------------


def check_field_values(data: dict[str, Any]) -> dict[str, Any]:
    """Reject clearly invalid values before anything else runs."""
    errors: list[str] = []

    numeric_positive = [
        ("applicant_annual_income", "applicant_annual_income must be greater than 0"),
        ("total_property_value", "total_property_value must be greater than 0"),
        ("property_value", "property_value must be greater than 0"),
        ("loan_amount", "loan_amount must be greater than 0"),
    ]
    for field, message in numeric_positive:
        try:
            if float(data.get(field, 0)) <= 0:
                errors.append(message)
        except (TypeError, ValueError):
            errors.append(f"{field} must be numeric")

    try:
        if float(data.get("deposit_amount", 0)) < 0:
            errors.append("deposit_amount cannot be negative")
    except (TypeError, ValueError):
        errors.append("deposit_amount must be numeric")

    try:
        if int(data.get("preferred_term", 0)) <= 0:
            errors.append("preferred_term must be a positive integer")
    except (TypeError, ValueError):
        errors.append("preferred_term must be an integer")

    try:
        credit = int(data.get("credit_score", 0))
        if credit < 300 or credit > 900:
            errors.append("credit_score should be within 300-900")
    except (TypeError, ValueError):
        errors.append("credit_score must be an integer")

    passed = not errors
    return {
        "passed": passed,
        "errors": errors,
        "reason": "All supplied values are valid" if passed else errors[0],
    }


def check_credit_score(data: dict[str, Any]) -> dict[str, Any]:
    credit = int(_to_float(data.get("credit_score"), 650))
    passed = credit >= MIN_CREDIT_SCORE

    if credit < 580:
        band, score_range, risk_points = "very_poor", "300-579", 5
    elif credit < 620:
        band, score_range, risk_points = "poor", "580-619", 4
    elif credit < 660:
        band, score_range, risk_points = "fair", "620-659", 3
    elif credit < 740:
        band, score_range, risk_points = "good", "660-739", 2
    elif credit < 800:
        band, score_range, risk_points = "very_good", "740-799", 1
    else:
        band, score_range, risk_points = "excellent", "800-900", 0

    return {
        "passed": passed,
        "credit_score": credit,
        "min_required": MIN_CREDIT_SCORE,
        "distance_from_min": credit - MIN_CREDIT_SCORE,
        "score_band": band,
        "score_range": score_range,
        "risk_points": risk_points,
        "reason": (
            "Credit score meets minimum threshold"
            if passed
            else f"Credit score below policy minimum ({MIN_CREDIT_SCORE})"
        ),
    }


def check_debt_to_income(data: dict[str, Any]) -> dict[str, Any]:
    """Monthly debt obligations against monthly income."""
    total_monthly_income = (
        _to_float(data.get("applicant_annual_income"))
        + _to_float(data.get("other_source_of_income"))
    ) / 12

    explicit_monthly_emi = sum(
        _to_float(data.get(key))
        for key in (
            "existing_loan_emi",
            "other_loan_emi",
            "credit_card_emi",
            "personal_loan_emi",
            "auto_loan_emi",
            "home_loan_emi",
        )
    )

    existing_debts = _to_float(data.get("existing_debts"))
    fallback_debt_service = existing_debts / 60 if existing_debts > 0 else 0.0
    monthly_loan_obligations = max(explicit_monthly_emi, fallback_debt_service)

    if total_monthly_income <= 0:
        return {
            "passed": False,
            "dti_ratio": 1.0,
            "dti_percent": 100.0,
            "max_allowed_dti_percent": MAX_DTI_PERCENT,
            "dti_band": "critical",
            "dti_range": ">50%",
            "risk_points": 5,
            "monthly_income_total": 0.0,
            "monthly_loan_obligations": round(monthly_loan_obligations, 2),
            "reason": "Monthly income is missing or invalid for DTI calculation",
        }

    dti_ratio = monthly_loan_obligations / total_monthly_income

    if dti_ratio <= 0.20:
        band, dti_range, risk_points = "excellent", "0-20%", 0
    elif dti_ratio <= 0.30:
        band, dti_range, risk_points = "good", "20-30%", 1
    elif dti_ratio <= 0.36:
        band, dti_range, risk_points = "moderate", "30-36%", 2
    elif dti_ratio <= 0.43:
        band, dti_range, risk_points = "elevated", "36-43%", 3
    elif dti_ratio <= 0.50:
        band, dti_range, risk_points = "high", "43-50%", 4
    else:
        band, dti_range, risk_points = "critical", ">50%", 5

    passed = dti_ratio <= (MAX_DTI_PERCENT / 100)
    return {
        "passed": passed,
        "dti_ratio": round(dti_ratio, 4),
        "dti_percent": round(dti_ratio * 100, 2),
        "max_allowed_dti_percent": MAX_DTI_PERCENT,
        "dti_band": band,
        "dti_range": dti_range,
        "risk_points": risk_points,
        "monthly_income_total": round(total_monthly_income, 2),
        "monthly_loan_obligations": round(monthly_loan_obligations, 2),
        "reason": (
            "DTI is within policy threshold"
            if passed
            else f"DTI exceeds policy threshold ({MAX_DTI_PERCENT:.0f}%)"
        ),
    }


def check_loan_to_value(data: dict[str, Any]) -> dict[str, Any]:
    """LTV against the purchase property value.

    Note `ltv` is a PERCENTAGE (e.g. 50.0), not a ratio — callers must compare
    it against 90/80, not 0.90/0.80. Getting that wrong was BUGFIX 2 below.
    """
    property_value = _to_float(data.get("property_value"))
    loan_amount = _to_float(data.get("loan_amount"))

    if property_value <= 0:
        return {
            "passed": False,
            "ltv": 100.0,
            "max_by_ltv": 0.0,
            "max_allowed_ltv": MAX_LTV_PERCENT,
            "distance_from_max_ltv": -5.0,
            "ltv_band": "critical",
            "ltv_range": ">95%",
            "risk_points": 5,
            "reason": "Invalid property value",
        }

    ltv = (loan_amount / property_value) * 100

    if ltv <= 60:
        band, ltv_range, risk_points = "excellent", "0-60%", 0
    elif ltv <= 75:
        band, ltv_range, risk_points = "very_good", "60-75%", 1
    elif ltv <= 80:
        band, ltv_range, risk_points = "good", "75-80%", 2
    elif ltv <= 90:
        band, ltv_range, risk_points = "elevated", "80-90%", 3
    elif ltv <= 95:
        band, ltv_range, risk_points = "high", "90-95%", 4
    else:
        band, ltv_range, risk_points = "critical", ">95%", 5

    passed = ltv <= MAX_LTV_PERCENT
    return {
        "passed": passed,
        "ltv": round(ltv, 2),
        "max_by_ltv": round(property_value * (MAX_LTV_PERCENT / 100), 2),
        "max_allowed_ltv": MAX_LTV_PERCENT,
        "distance_from_max_ltv": round(MAX_LTV_PERCENT - ltv, 2),
        "ltv_band": band,
        "ltv_range": ltv_range,
        "risk_points": risk_points,
        "reason": (
            "Requested loan is within LTV policy"
            if passed
            else f"Requested loan exceeds maximum {MAX_LTV_PERCENT:.0f}% LTV"
        ),
    }


def _age_from_dob(dob_raw: str) -> int | None:
    if not dob_raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            dob = datetime.strptime(dob_raw, fmt).date()
            break
        except ValueError:
            continue
    else:
        return None

    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def check_age_eligibility(data: dict[str, Any]) -> dict[str, Any]:
    dob_raw = str(data.get("date_of_birth") or "").strip()
    age = _age_from_dob(dob_raw)
    if age is None:
        age = int(_to_float(data.get("age", data.get("applicant_age")), 35))

    term = int(_to_float(data.get("preferred_term"), 25))
    age_at_end = age + term
    passed = age >= 18 and age_at_end <= 75
    return {
        "passed": passed,
        "applicant_age": age,
        "date_of_birth": dob_raw,
        "age_at_term_end": age_at_end,
        "reason": (
            "Age policy passed"
            if passed
            else "Age policy failed (mortgage must finish by age 75)"
        ),
    }


def check_loan_amount(data: dict[str, Any]) -> dict[str, Any]:
    amount = _to_float(data.get("loan_amount"))
    passed = MIN_LOAN <= amount <= MAX_LOAN
    return {
        "passed": passed,
        "min_loan": MIN_LOAN,
        "max_loan": MAX_LOAN,
        "reason": (
            "Loan amount is within product limits"
            if passed
            else f"Loan amount must be between £{MIN_LOAN:,.0f} and £{MAX_LOAN:,.0f}"
        ),
    }


def check_mortgage_term(data: dict[str, Any]) -> dict[str, Any]:
    term = int(_to_float(data.get("preferred_term"), 25))
    passed = MIN_TERM_YEARS <= term <= MAX_TERM_YEARS
    return {
        "passed": passed,
        "min_term": MIN_TERM_YEARS,
        "max_term": MAX_TERM_YEARS,
        "reason": (
            "Term is within allowed range"
            if passed
            else f"Term must be between {MIN_TERM_YEARS} and {MAX_TERM_YEARS} years"
        ),
    }


def run_checks(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "validate": check_field_values(data),
        "credit": check_credit_score(data),
        "dti": check_debt_to_income(data),
        "ltv": check_loan_to_value(data),
        "age": check_age_eligibility(data),
        "loan": check_loan_amount(data),
        "term": check_mortgage_term(data),
    }


# --- Offer ------------------------------------------------------------------


def calc_monthly_payment(amount: float, term_years: int, annual_rate_pct: float) -> float:
    """Standard amortised monthly payment."""
    monthly_rate = (annual_rate_pct / 100) / 12
    n = term_years * 12
    if n <= 0:
        return 0.0
    if monthly_rate == 0:
        return round(amount / n, 2)
    return round(
        amount * (monthly_rate * (1 + monthly_rate) ** n) / ((1 + monthly_rate) ** n - 1),
        2,
    )


def calculate_offer(
    data: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    failed_checks: list[str],
) -> dict[str, Any]:
    requested_loan = _to_float(data.get("loan_amount"))
    annual_income = _to_float(data.get("applicant_annual_income"))
    preferred_term = int(_to_float(data.get("preferred_term")))
    age = int(checks.get("age", {}).get("applicant_age", 35))

    max_loan_by_income = max(0.0, annual_income * INCOME_MULTIPLE)

    # BUGFIX 1: the original read `max_loan_by_ltv`, a key the LTV check never
    # returns — the lookup always missed and silently fell back to the
    # requested loan, so the LTV cap was never applied to the offer.
    max_loan_by_ltv = _to_float(
        checks.get("ltv", {}).get("max_by_ltv"), requested_loan
    ) or requested_loan

    max_loan = round(min(max_loan_by_income, max_loan_by_ltv), 2)
    if max_loan <= 0:
        max_loan = requested_loan
    max_loan = min(max_loan, MAX_LOAN)

    credit_score = int(_to_float(data.get("credit_score")))
    if credit_score >= 780:
        base_rate = 4.20
    elif credit_score >= 740:
        base_rate = 4.55
    elif credit_score >= 700:
        base_rate = 4.95
    else:
        base_rate = 5.45

    # BUGFIX 2: `ltv` is a percentage (e.g. 50.0). The original compared it
    # against ratios (`> 0.90`), which is true for virtually every applicant,
    # so everyone silently picked up the +0.35 high-LTV loading.
    requested_ltv = _to_float(checks.get("ltv", {}).get("ltv"))
    if requested_ltv > 90:
        base_rate += 0.35
    elif requested_ltv > 80:
        base_rate += 0.15

    if "credit" in failed_checks:
        base_rate += 0.40
    if "dti" in failed_checks:
        base_rate += 0.20

    employment_type = str(data.get("employment_type", "")).strip().lower()
    if employment_type in {"self employed", "self-employed", "contractor", "contract"}:
        base_rate += 0.10

    offered_rate = round(max(RATE_FLOOR, base_rate), 2)
    max_term = MAX_TERM_YEARS if age < 55 else 30
    offered_term = min(preferred_term if preferred_term > 0 else max_term, max_term)
    offered_amount = round(min(requested_loan, max_loan), 2)

    return {
        "amount": offered_amount,
        "requested_amount": requested_loan,
        "term": offered_term,
        "rate": offered_rate,
        "monthly": calc_monthly_payment(offered_amount, offered_term, offered_rate),
        "ltv": requested_ltv,
        "max_loan": max_loan,
        "capped": offered_amount < requested_loan,
    }


def summarise(
    data: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    offer: dict[str, Any],
) -> tuple[list[str], list[str], str]:
    """Plain-language strengths/concerns and a rationale, derived from the
    numbers — no LLM involved."""
    strengths: list[str] = []
    concerns: list[str] = []

    credit_score = int(_to_float(data.get("credit_score")))
    dti_percent = _to_float(checks.get("dti", {}).get("dti_percent"))
    ltv = _to_float(checks.get("ltv", {}).get("ltv"))

    if credit_score >= 740:
        strengths.append("strong credit score")
    elif credit_score < 650:
        concerns.append("credit score is below prime range")

    if 0 < dti_percent <= 35:
        strengths.append("healthy debt-to-income position")
    elif dti_percent > 45:
        concerns.append("higher debt-to-income pressure")

    if 0 < ltv <= 60:
        strengths.append("low loan-to-value exposure")
    elif ltv > 85:
        concerns.append("higher loan-to-value exposure")

    if not offer.get("capped"):
        strengths.append("requested loan is within affordability limits")
    else:
        concerns.append("requested loan exceeded the affordability cap and was reduced")

    if not strengths:
        strengths.append("application is supportable within lending policy")
    if not concerns:
        concerns.append("no major affordability concern identified")

    rationale = (
        "After reviewing affordability, credit profile, loan-to-value position and "
        "requested term, the bank is prepared to make a risk-adjusted mortgage offer. "
        f"Key strengths: {', '.join(strengths)}. "
        f"Key considerations: {', '.join(concerns)}."
    )
    return strengths, concerns, rationale


def underwrite(data: dict[str, Any]) -> dict[str, Any]:
    """Run every check, decide, and price the offer."""
    checks = run_checks(data)

    failed_checks = [name for name, result in checks.items() if not result.get("passed")]
    hard_failures = [name for name in failed_checks if name in HARD_FAIL_CHECKS]
    approved = not hard_failures

    total_risk_points = sum(
        int(checks.get(name, {}).get("risk_points", 0)) for name in ("credit", "dti", "ltv")
    )

    if approved:
        offer = calculate_offer(data, checks, failed_checks)
        strengths, concerns, rationale = summarise(data, checks, offer)
        decision_reason = (
            "All checks passed"
            if not failed_checks
            else "Proceeding with risk-adjusted offer"
        )
    else:
        offer = None
        strengths, concerns = [], [
            checks[name].get("reason", name) for name in hard_failures
        ]
        rationale = (
            "The application cannot proceed because it failed mandatory policy checks: "
            + ", ".join(CHECK_LABELS.get(n, n) for n in hard_failures)
            + "."
        )
        decision_reason = "Critical policy checks failed"

    return {
        "approved": approved,
        "decision_reason": decision_reason,
        "checks": checks,
        "failed_checks": failed_checks,
        "hard_failures": hard_failures,
        "total_risk_points": total_risk_points,
        "offer": offer,
        "strengths": strengths,
        "concerns": concerns,
        "rationale": rationale,
    }
