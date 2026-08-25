"""Lloyds Bank core service.

The bank's own system: it owns the customer book, runs underwriting, and
serves the internal Command Centre dashboard. The ChatGPT-facing MCP backend
is a separate process that talks to this one over HTTP.

Run:  python -m uvicorn bank.app:app --port 4000
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from bank import affordability, records, underwriting
from bank.customers import get_customer, list_customers

# Same as backend/config.py: honour .env however this service is launched.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("bank")

PUBLIC_DIR = Path(__file__).resolve().parent / "public"
BANK_PORT = int(os.getenv("BANK_PORT", "4000"))

# Same local-dev-only switch the backend uses for its debug endpoints — never
# enable in a real deployment.
DEBUG_ENDPOINTS = os.getenv("DEBUG_EXPOSE_REFERENCE_ID", "false").lower() == "true"

app = FastAPI(title="Lloyds Bank Core", docs_url="/docs", redoc_url=None)

# The Command Centre is served from this same origin, but allow everything so
# the dashboard still works if it's opened through a tunnel.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UnderwriteRequest(BaseModel):
    session_id: str
    email: str
    loan_amount: float = Field(gt=0)
    # The property being purchased now, which the bank has no prior record of.
    property_value: float = Field(gt=0)
    preferred_term: int = Field(ge=1, le=50)


class ChatEvent(BaseModel):
    sender: str
    text: str


class AffordabilityRequest(BaseModel):
    combined_annual_income: float = Field(gt=0)
    # Exactly one of these two — a percentage (e.g. 15 for 15%, not a ratio)
    # or a cash deposit — is required; affordability.calculate_affordability
    # enforces that and derives the other.
    deposit_percentage: float | None = Field(default=None, ge=0, lt=100)
    deposit_amount: float | None = Field(default=None, ge=0)
    term_years: int | None = Field(default=None, ge=1, le=50)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "service": "Lloyds Bank Core"}


@app.post("/api/_debug/reset", include_in_schema=False)
async def debug_reset() -> dict:
    """Local-dev-only: wipe all session records so the Command Centre starts
    clean (scripts/test_mcp.py otherwise leaves its test sessions in the
    sidebar forever). Disabled unless DEBUG_EXPOSE_REFERENCE_ID=true."""
    if not DEBUG_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not found")
    records.reset()
    return {"status": "reset"}


@app.get("/api/customer", tags=["customers"])
async def customer_lookup(email: str) -> dict:
    """Resolve a customer by email — this is what makes the email in chat
    identify a real account rather than just being a verification destination."""
    customer = get_customer(email)
    if customer is None:
        raise HTTPException(status_code=404, detail="No customer found for that email address")
    return customer


@app.get("/api/customers", tags=["customers"], include_in_schema=False)
async def customers() -> list[dict]:
    return list_customers()


@app.post("/api/underwrite", tags=["underwriting"])
async def underwrite(payload: UnderwriteRequest) -> dict:
    customer = get_customer(payload.email)
    if customer is None:
        raise HTTPException(status_code=404, detail="No customer found for that email address")

    # The customer profile supplies income, credit and standing debts; chat
    # supplies what the customer chose for this purchase. Applying the
    # application on top means the property they are buying now — not whatever
    # the bank had on file — is what LTV is measured against.
    application = {
        "loan_amount": float(payload.loan_amount),
        "property_value": float(payload.property_value),
        "preferred_term": int(payload.preferred_term),
    }
    data = {**customer, **application}

    result = underwriting.underwrite(data)
    records.record_underwriting(payload.session_id, customer, application, result)

    logger.info(
        "Underwrote %s for %s: approved=%s rate=%s",
        payload.session_id,
        customer["customer_id"],
        result["approved"],
        (result["offer"] or {}).get("rate"),
    )
    return {
        "session_id": payload.session_id,
        "customer": {
            "customer_id": customer["customer_id"],
            "full_name": customer["full_name"],
            "email": customer["email"],
            "applicant_annual_income": customer["applicant_annual_income"],
            "credit_score": customer["credit_score"],
            "deposit_amount": customer["deposit_amount"],
            # On file at the bank — kept distinct from the property being
            # bought, which arrives on the application below.
            "recorded_property_value": customer["property_value"],
        },
        "application": application,
        **result,
    }


@app.post("/api/affordability", tags=["underwriting"])
async def affordability_enquiry(payload: AffordabilityRequest) -> dict:
    """Indicative "what could I borrow" estimate — no customer record, no
    identity check, not a binding offer. Kept off the underwriting/session
    machinery entirely since nothing here is tied to an application yet."""
    try:
        result = affordability.calculate_affordability(
            combined_annual_income=payload.combined_annual_income,
            deposit_percentage=payload.deposit_percentage,
            deposit_amount=payload.deposit_amount,
            term_years=payload.term_years,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


@app.post("/api/session/{session_id}/chat", tags=["sessions"])
async def session_chat(session_id: str, event: ChatEvent) -> dict:
    """Mirror of what was said over MCP, so the Command Centre feed is live."""
    records.append_chat(session_id, event.sender, event.text)
    return {"status": "ok"}


@app.get("/api/sessions", tags=["sessions"])
async def sessions() -> dict:
    return records.all_records()


@app.get("/api/sessions/{session_id}", tags=["sessions"])
async def session_detail(session_id: str) -> dict:
    record = records.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    return record


@app.get("/", include_in_schema=False)
async def command_centre():
    return FileResponse(PUBLIC_DIR / "command_centre.html")


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Lloyds Bank Core on port %s", BANK_PORT)
    uvicorn.run(app, host="0.0.0.0", port=BANK_PORT)
