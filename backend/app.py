"""FastAPI application for the Lloyds Banking Demo Plugin.

Exposes:
  * REST endpoints under /api/*      -> used by Actions and the Lloyds frontend
  * MCP Streamable HTTP endpoint /mcp -> used by ChatGPT MCP connectors
  * Legacy ChatGPT plugin files       -> /.well-known/ai-plugin.json, /openapi.yaml
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.routing import Route

from backend import auth, config
from backend.logging_conf import configure_logging
from backend.mcp_server import mcp_asgi_app, session_manager
from backend.models import (
    MortgageOfferResponse,
    MortgagePromptResponse,
    MortgageSalaryRequest,
    TransactionSummaryResponse,
)
from backend.mock_data import calculate_mortgage_offer, get_transaction_summary
from backend import store
from backend.ui_cards import (
    render_mortgage_offer_card,
    render_mortgage_salary_prompt,
    render_transaction_card,
)

configure_logging()
logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Lloyds Banking Demo Plugin backend")
    async with session_manager.run():
        yield
    logger.info("Shutting down Lloyds Banking Demo Plugin backend")


app = FastAPI(
    title=config.PLUGIN_NAME,
    description="Demo banking plugin exposing transaction and mortgage tools to ChatGPT.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request, exc: ValueError):
    logger.warning("Bad request: %s", exc)
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "plugin": config.PLUGIN_NAME}


@app.get("/api/_debug/otp", include_in_schema=False)
async def debug_otp() -> dict:
    """Local-dev-only: returns the currently pending OTP so scripts/test_mcp.py
    can verify the auth flow without a real inbox. Disabled unless
    DEBUG_EXPOSE_OTP=true — never enable this in a real deployment."""
    if not config.DEBUG_EXPOSE_OTP:
        raise HTTPException(status_code=404, detail="Not found")
    state = auth.peek_state()
    if state is None:
        raise HTTPException(status_code=404, detail="No pending OTP")
    return state


@app.post("/api/_debug/reset", include_in_schema=False)
async def debug_reset() -> dict:
    """Local-dev-only: clear verification and application state so the test
    suite starts from a known point. Disabled unless DEBUG_EXPOSE_OTP=true."""
    if not config.DEBUG_EXPOSE_OTP:
        raise HTTPException(status_code=404, detail="Not found")
    auth.reset()
    store.reset()
    return {"status": "reset"}


# --- REST API used by ChatGPT Actions and the Lloyds frontend ---------------


@app.get(
    "/api/transactions",
    response_model=TransactionSummaryResponse,
    tags=["transactions"],
    summary="List recent transactions",
    description=(
        "Returns the user's recent transactions plus a spending summary "
        "(total spend, count, latest transaction, spend by category). Call "
        "this whenever the user asks to see, list, or review their "
        "transactions or transaction history. Render the `ui_card` field "
        "verbatim as Markdown to present a rich banking-style card."
    ),
    operation_id="listTransactions",
)
async def list_transactions() -> TransactionSummaryResponse:
    logger.info("REST call: GET /api/transactions")
    summary = get_transaction_summary()
    web_app_url = f"{config.FRONTEND_BASE_URL}/transactions"
    return TransactionSummaryResponse(
        **summary,
        lloyds_web_app_url=web_app_url,
        ui_card=render_transaction_card(summary, web_app_url),
    )


@app.post(
    "/api/mortgage/start",
    response_model=MortgagePromptResponse,
    tags=["mortgage"],
    summary="Start the mortgage application flow",
    description=(
        "Begins the interactive mortgage application. Call this the moment "
        "the user says they want a mortgage or home loan. Ask the user the "
        "returned question and wait for their reply, then call "
        "/api/mortgage/estimate with their annual salary."
    ),
    operation_id="startMortgageApplication",
)
async def start_mortgage() -> MortgagePromptResponse:
    logger.info("REST call: POST /api/mortgage/start")
    return MortgagePromptResponse(message=render_mortgage_salary_prompt())


@app.post(
    "/api/mortgage/estimate",
    response_model=MortgageOfferResponse,
    tags=["mortgage"],
    summary="Calculate a mortgage offer from annual salary",
    description=(
        "Calculates a mortgage offer (loan amount, interest rate, term, "
        "estimated monthly payment) from the user's annual salary in GBP. "
        "Call this after the user replies with their salary following "
        "/api/mortgage/start. Render the `ui_card` field verbatim as "
        "Markdown to present a rich banking-style offer card."
    ),
    operation_id="calculateMortgageOffer",
)
async def estimate_mortgage(payload: MortgageSalaryRequest) -> MortgageOfferResponse:
    logger.info("REST call: POST /api/mortgage/estimate salary=%s", payload.annual_salary)
    try:
        offer = calculate_mortgage_offer(payload.annual_salary)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    store.save_application(offer)
    web_app_url = f"{config.FRONTEND_BASE_URL}/mortgage?application_id={offer['application_id']}"
    return MortgageOfferResponse(
        **offer,
        lloyds_web_app_url=web_app_url,
        ui_card=render_mortgage_offer_card(offer, web_app_url),
    )


@app.get(
    "/api/mortgage/latest",
    tags=["mortgage"],
    summary="Fetch the most recently generated mortgage offer",
    description="Used by the Lloyds web app to display the last offer generated via chat.",
    include_in_schema=False,
)
async def latest_mortgage_application() -> dict:
    offer = store.get_latest_application()
    if offer is None:
        raise HTTPException(status_code=404, detail="No mortgage application yet")
    return offer


@app.get(
    "/api/mortgage/{application_id}",
    tags=["mortgage"],
    summary="Fetch a mortgage application by id",
    description="Used by the Lloyds web app to display a specific application's details.",
    include_in_schema=False,
)
async def get_mortgage_application(application_id: str) -> dict:
    offer = store.get_application(application_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return offer


# --- Legacy ChatGPT plugin manifest + OpenAPI spec ---------------------------


@app.get("/.well-known/ai-plugin.json", include_in_schema=False)
async def ai_plugin_manifest():
    path = PLUGIN_DIR / "ai-plugin.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="ai-plugin.json not found")
    return FileResponse(path, media_type="application/json")


@app.get("/openapi.yaml", include_in_schema=False)
async def openapi_yaml():
    path = PLUGIN_DIR / "openapi.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="openapi.yaml not found")
    return FileResponse(path, media_type="text/yaml")


@app.get("/logo.png", include_in_schema=False)
async def logo():
    path = PLUGIN_DIR / "logo.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="logo.png not found")
    return FileResponse(path, media_type="image/png")


# --- MCP Streamable HTTP endpoint --------------------------------------------
# Registered as raw ASGI routes so the endpoint answers on exactly `/mcp`
# (and `/mcp/`) without a 307 redirect, which some MCP clients dislike.
for _mcp_path in ("/mcp", "/mcp/"):
    app.router.routes.append(
        Route(_mcp_path, endpoint=mcp_asgi_app, methods=["GET", "POST", "DELETE"])
    )
