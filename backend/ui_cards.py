"""Renders rich, premium-feeling Markdown 'cards' for display inside the ChatGPT
conversation. ChatGPT Actions and MCP tool results are consumed by the model and
rendered as Markdown, so this module is where the polished banking UI is authored.
"""
from __future__ import annotations


def render_transaction_card(summary: dict, web_app_url: str) -> str:
    txns = summary["transactions"]
    latest = summary["latest_transaction"]

    rows = "\n".join(
        f"| {t['icon']} **{t['merchant']}** | {t['category']} | £{t['amount']:,.2f} | "
        f"{t['date'][:10]} |"
        for t in txns
    )

    category_lines = "\n".join(
        f"- **{cat}**: £{amt:,.2f}" for cat, amt in summary["spending_by_category"].items()
    )

    return f"""### 🏦 Lloyds — Your Recent Transactions

<br>

| 💷 Total Spending | 🧾 Transactions | ⏱️ Latest |
|---|---|---|
| **£{summary['total_spending']:,.2f}** | **{summary['transaction_count']}** | {latest['icon']} {latest['merchant']} — £{latest['amount']:,.2f} |

---

#### 📋 Transaction History

| Merchant | Category | Amount | Date |
|---|---|---|---|
{rows}

---

#### 📊 Spending Summary

{category_lines}

- **Average transaction:** £{summary['average_transaction']:,.2f}

---

### 🔗 [**Open in Lloyds Web App →**]({web_app_url})

*View your full dashboard, spending analytics, and detailed transaction breakdown in the Lloyds web app.*
"""


def render_mortgage_salary_prompt() -> str:
    return """### 🏡 Lloyds Mortgage Application

Great — let's get your personalised mortgage estimate started.

**What is your annual salary?**

*(e.g. "£50,000" or "50000") — I'll use this to calculate your estimated loan amount, interest rate, and monthly repayment.*
"""


# --- Mortgage journey: field-by-field collection + OTP auth -----------------

MORTGAGE_FIELD_QUESTIONS = {
    "loan_amount": "How much would you like to borrow?",
    "property_value": "What is the value of the property you're looking to buy?",
    "preferred_term": "Over how many years would you like to repay it? (5-35)",
}


def render_mortgage_field_question(field: str, error: str | None = None) -> str:
    question = MORTGAGE_FIELD_QUESTIONS.get(field, f"Could you share your {field.replace('_', ' ')}?")
    prefix = f"⚠️ {error}\n\n" if error else ""
    return f"""### 🏡 Lloyds Mortgage Application

{prefix}**{question}**
"""


def render_email_prompt(error: str | None = None) -> str:
    prefix = f"⚠️ {error}\n\n" if error else ""
    return f"""### 🔒 Identity Verification

{prefix}Please enter your email ID for authentication
"""


def render_otp_prompt() -> str:
    return """### 🔒 Identity Verification

We've sent a 6-digit verification code to your email.

Please enter the OTP for authentication
"""


def render_otp_cancelled(application_id: str | None = None) -> str:
    reference = (
        f"Application `{application_id}` has been cancelled and not submitted.\n\n"
        if application_id
        else ""
    )
    # The verdict leads, in one blunt sentence — assistants tend to compress
    # these cards to their first line, and "the OTP is wrong" is the one thing
    # the customer must not lose.
    return f"""### ❌ Invalid OTP — the code you entered is wrong

The OTP you entered does not match the code we emailed you, so this request has
been cancelled for your security.

{reference}The code that was issued is no longer valid. To continue, please start again and
we'll send you a new one.
"""


def render_declined_card(application_id: str, result: dict) -> str:
    """Shown when the bank's mandatory policy checks fail."""
    reasons = "\n".join(f"- {reason}" for reason in result.get("concerns", []))
    return f"""### ❌ We're unable to proceed with this application

**Application ID:** `{application_id}`

{result.get('rationale', 'The application did not meet our lending policy.')}

{reasons}

---

This decision is based on our standard lending policy. If your circumstances
change — or you'd like to discuss it with an advisor — please get in touch and
we'll be happy to review it with you.
"""


def render_mortgage_success_card(offer: dict, web_app_url: str, image_url: str) -> str:
    greeting = (
        f"Thank you, {offer['customer_name']}. "
        if offer.get("customer_name")
        else ""
    )

    # Underwriting figures are only present when the bank priced this offer.
    assessment_rows = []
    if offer.get("credit_score") is not None:
        assessment_rows.append(f"| **Credit Score** | {offer['credit_score']} |")
    if offer.get("ltv") is not None:
        assessment_rows.append(f"| **Loan-to-Value** | {offer['ltv']:.2f}% |")
    if offer.get("dti_percent") is not None:
        assessment_rows.append(f"| **Debt-to-Income** | {offer['dti_percent']:.2f}% |")

    assessment = ""
    if assessment_rows:
        assessment = (
            "\n#### 📊 Underwriting Assessment\n\n| | |\n|---|---|\n"
            + "\n".join(assessment_rows)
            + "\n\n---\n"
        )

    # Everything that matters is in the opening sentence: assistants routinely
    # compress a long card down to its first line, and an approval that loses
    # the amount, rate and term tells the customer nothing.
    return f"""### ✅ Mortgage application submitted — approved

{greeting}We have approved **£{offer['loan_amount']:,.2f}** over **{offer['term_years']} years** at **{offer['interest_rate']}% APR** — about **£{offer['estimated_monthly_payment']:,.2f} per month**.

**Application ID:** `{offer['application_id']}`. Our Relationship Manager will contact you shortly to take this forward.

---

![Mortgage application submitted]({image_url})

---

#### 💰 Your Estimated Offer

| | |
|---|---|
| **Loan Amount** | £{offer['loan_amount']:,.2f} |
| **Interest Rate** | {offer['interest_rate']}% (fixed) |
| **Term** | {offer['term_years']} years |
| **Estimated Monthly Payment** | **£{offer['estimated_monthly_payment']:,.2f}** |

---
{assessment}
> {offer['eligibility']['notes']}

---

### 🙏 Thank You for Choosing Lloyds

We appreciate you taking the time to apply. Your dedicated Relationship Manager
will be in touch to guide you through the next steps and answer any questions.

🔗 [**Continue in Lloyds Web App →**]({web_app_url})
"""


def render_mortgage_offer_card(offer: dict, web_app_url: str) -> str:
    next_steps = "\n".join(f"{i}. {step}" for i, step in enumerate(offer["next_steps"], start=1))

    return f"""### ✅ Mortgage Application Initiated

Your mortgage application **{offer['application_id']}** has been created based on an annual salary of **£{offer['annual_salary']:,.2f}**.

---

#### 💰 Estimated Offer

| | |
|---|---|
| **Loan Amount** | £{offer['loan_amount']:,.2f} |
| **Interest Rate** | {offer['interest_rate']}% (fixed) |
| **Term** | {offer['term_years']} years |
| **Estimated Monthly Payment** | **£{offer['estimated_monthly_payment']:,.2f}** |

---

#### 📐 Loan Breakdown

- **Total repayable over term:** £{offer['total_repayable']:,.2f}
- **Total interest:** £{offer['total_interest']:,.2f}
- **Loan-to-income multiple:** {offer['eligibility']['loan_to_income_multiple']}x
- **Eligibility:** {"✅ Eligible" if offer['eligibility']['eligible'] else "❌ Not eligible"}

> {offer['eligibility']['notes']}

---

#### 📝 Next Steps

{next_steps}

---

### 🔗 [**Continue in Lloyds Web App →**]({web_app_url})

*Track your application status, view full loan details, and check your eligibility summary in the Lloyds web app.*
"""
