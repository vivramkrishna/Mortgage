"""MCP server exposing the Lloyds Banking Demo tools over Streamable HTTP.

Uses the low-level MCP Server API (rather than FastMCP) so that we have exact
control over what MCP Apps requires in order to render a real inline UI
component instead of plain text:

  1. The tool references a UI resource, via both `_meta["ui"]["resourceUri"]`
     (the MCP Apps standard) and `_meta["openai/outputTemplate"]` (the ChatGPT
     alias). The resource is served with the MCP Apps UI MIME type
     `text/html;profile=mcp-app` — see config.WIDGET_MIME_MODE to fall back to
     the older `text/html+skybridge` for legacy ChatGPT builds.
  2. Every tool *result* repeats that same `_meta` — advertising it only in
     tools/list is not enough.
  3. The tool result carries `structuredContent`, which the component receives
     via `ui/notifications/tool-result` (or `window.openai.toolOutput`).

Reference: https://developers.openai.com/plugins/build/chatgpt-ui

`list_transactions` renders a widget once verified — the mortgage tools
and the pre-auth prompts return Markdown only. Both flows share a single
process-global reference ID session (backend/auth.py): verifying once (whichever flow
triggered it) is reused by the other for the rest of the chat session.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import AnyUrl

from backend import auth, bank_client, config, request_log, store, transactions_data
from backend.ui_cards import (
    MORTGAGE_FIELD_QUESTIONS,
    render_affordability_card,
    render_affordability_field_question,
    render_declined_card,
    render_email_prompt,
    render_mortgage_field_question,
    render_mortgage_success_card,
    render_reference_id_cancelled,
    render_reference_id_prompt,
)

logger = logging.getLogger(__name__)

# What the customer actually chooses, asked one at a time. Everything else the
# underwriter needs — income, credit score, date of birth, employment, deposit,
# existing debts — comes from the bank's customer record, resolved from the
# email address they verify with.
#
# `property_value` is asked rather than read from the record because it is the
# property they are buying now, which the bank has no way to know, and it is
# what the loan-to-value check is computed against.
MORTGAGE_FIELDS = ["loan_amount", "property_value", "preferred_term"]

# Affordability enquiry (`check_affordability`) is a distinct, lighter-weight
# flow from the mortgage application above — no identity verification, no
# customer record, no binding offer. Needs combined_annual_income plus
# EITHER deposit_percentage or deposit_amount; kept on its own draft
# namespace (backend/store.py) so the two conversations can never be
# confused with each other.

WIDGETS_DIR = Path(__file__).resolve().parent / "widgets"

TRANSACTIONS_WIDGET_URI = "ui://widget/transactions.html"
WIDGET_MIME = config.WIDGET_MIME_TYPE

server: Server = Server(config.PLUGIN_NAME)


def _load_widget(filename: str) -> str:
    return (WIDGETS_DIR / filename).read_text(encoding="utf-8")


# Widget metadata is emitted in BOTH supported dialects, because ChatGPT builds
# differ in which one they honour:
#   * `ui.resourceUri`            — the MCP Apps standard
#   * `openai/outputTemplate`     — the ChatGPT alias for the same thing
# It must also appear in THREE places: the tool definition (tools/list), the
# tool result (tools/call), and the resource. Omitting it from the tool
# *result* is the classic failure mode — the client receives structuredContent
# but is never told which template to render, so it degrades to plain text.
TRANSACTIONS_WIDGET_META: dict[str, Any] = {
    "ui": {"resourceUri": TRANSACTIONS_WIDGET_URI},
    "openai/outputTemplate": TRANSACTIONS_WIDGET_URI,
    "openai/toolInvocation/invoking": "Fetching your transactions…",
    "openai/toolInvocation/invoked": "Loaded your transactions",
    "openai/widgetAccessible": True,
    "openai/resultCanProduceWidget": True,
}

# The widget is fully self-contained — it makes no network requests and loads
# no external resources, so every CSP allowlist stays empty.
_EMPTY_CSP: dict[str, Any] = {
    "connectDomains": [],
    "resourceDomains": [],
    "frameDomains": [],
}

WIDGET_RESOURCE_META: dict[str, Any] = {
    **TRANSACTIONS_WIDGET_META,
    "ui": {
        "resourceUri": TRANSACTIONS_WIDGET_URI,
        "prefersBorder": False,
        "csp": _EMPTY_CSP,
    },
    "openai/widgetDescription": (
        "A Lloyds banking card showing the user's recent transactions, "
        "total spending, average spend, and a spend-by-category breakdown."
    ),
    "openai/widgetPrefersBorder": False,
    "openai/widgetCSP": {"connect_domains": [], "resource_domains": []},
}


# --- Resources: the HTML widget templates -----------------------------------


@server.list_resources()
async def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri=AnyUrl(TRANSACTIONS_WIDGET_URI),
            name="Lloyds transactions widget",
            description="Inline banking UI showing recent transactions and spending summary.",
            mimeType=WIDGET_MIME,
            # NOTE: must be passed as the `_meta` alias — these models do not
            # set populate_by_name, so `meta=` would become an ignored extra.
            **{"_meta": WIDGET_RESOURCE_META},
        )
    ]


async def _handle_read_resource(req: types.ReadResourceRequest) -> types.ServerResult:
    """Registered manually rather than via @server.read_resource() so the
    returned contents can carry `_meta` (the decorator's ReadResourceContents
    helper has no field for it)."""
    uri = str(req.params.uri)
    if uri != TRANSACTIONS_WIDGET_URI:
        raise ValueError(f"Unknown resource: {uri}")

    return types.ServerResult(
        types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=AnyUrl(TRANSACTIONS_WIDGET_URI),
                    mimeType=WIDGET_MIME,
                    text=_load_widget("transactions.html"),
                    **{"_meta": WIDGET_RESOURCE_META},
                )
            ],
            **{"_meta": WIDGET_RESOURCE_META},
        )
    )


server.request_handlers[types.ReadResourceRequest] = _handle_read_resource


# --- Tools -------------------------------------------------------------------


_MORTGAGE_FIELD_SCHEMAS: dict[str, dict[str, Any]] = {
    "loan_amount": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "The amount the applicant wants to borrow, in GBP.",
    },
    "property_value": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Value of the property the applicant is buying, in GBP.",
    },
    "preferred_term": {
        "type": "integer",
        "minimum": 5,
        "maximum": 35,
        "description": "Preferred mortgage term in years (5-35).",
    },
}

_AFFORDABILITY_FIELD_SCHEMAS: dict[str, dict[str, Any]] = {
    "combined_annual_income": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Combined annual income of all applicants, in GBP.",
    },
    # Customers answer the deposit question either way — pass whichever the
    # user actually said; never convert one to the other yourself.
    "deposit_percentage": {
        "type": "number",
        "minimum": 0,
        "exclusiveMaximum": 100,
        "description": "Deposit saved, as a percentage of the property price (e.g. 15 for 15%). Use this OR deposit_amount, whichever the user gave.",
    },
    "deposit_amount": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Deposit saved, as a cash amount in GBP (e.g. 15000 for '£15,000 saved'). Use this OR deposit_percentage, whichever the user gave.",
    },
}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_transactions",
            title="List transactions",
            description=(
                "Answer ANY question about the user's bank transactions — "
                "listing, filtering, totals, averages, counts, min/max, "
                "balances, or breakdowns/top-N by category, merchant, "
                "subcategory or month. There is no separate tool per query "
                "type: pass the user's question (verbatim, lightly cleaned "
                "up) as `query` and this tool parses it itself, e.g. "
                "'show my shopping transactions', 'how much did I spend on "
                "groceries in August', 'what's my average transaction', "
                "'top 5 merchants', 'spending by category', 'cheapest "
                "transaction', 'transactions over £100'. Omit `query` (or "
                "call with no arguments) to show the most recent "
                "transactions. Requires identity verification — if the "
                "result asks the user to verify their identity, follow up "
                "with `submit_verification_email` then `verify_reference_id` "
                "(same shared verification flow the mortgage journey uses; "
                "if the user already verified earlier in this chat you "
                "won't be asked again), then call `list_transactions` again "
                "with the SAME `query`. Plain listing queries render as an "
                "interactive banking card — do not repeat the transaction "
                "data as a table in your reply, just add a one-line summary. "
                "Aggregation queries (totals, counts, averages, breakdowns, "
                "min/max, balance) return plain text with no card — relay "
                "that text as the answer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The user's transaction question, in their own words "
                            "(e.g. 'how much did I spend on Amazon last month'). "
                            "Omit to list the most recent transactions."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            **{"_meta": TRANSACTIONS_WIDGET_META},
        ),
        types.Tool(
            name="check_affordability",
            title="Check mortgage affordability",
            description=(
                "Give an INDICATIVE 'how much could I borrow / what could I "
                "afford' estimate. Call this the moment the user asks what "
                "they could afford, says something like 'we need a bigger "
                "home we can afford', or asks for a borrowing estimate — "
                "WITHOUT starting a full mortgage application. No identity "
                "verification, no customer record lookup, non-binding "
                "estimate only, never an offer.\n\n"
                "CRITICAL: every text this tool returns — the next question "
                "to ask, or the final quote — is your COMPLETE reply. Paste "
                "it verbatim, character for character: same words, same "
                "labels, same line order, same numbers. Do not add a "
                "preamble, example, explanation or closing sentence of your "
                "own. Do not merge it with another question. Do not rename "
                "any label (it is 'Mortgage amount', not 'Loan amount'; "
                "'Tenure', not 'Mortgage term'; etc — copy exactly as given, "
                "do not substitute a synonym).\n\n"
                "Needs combined_annual_income and EITHER deposit_percentage "
                "OR deposit_amount — pass through whichever unit the user "
                "gave, both in the same call if given together. Never "
                "convert one into the other yourself. Always pass the same "
                "`enquiry_id` (returned by the first call) on every "
                "follow-up call for this enquiry.\n\n"
                "NEVER answer an affordability question with your own "
                "estimate, range or general knowledge — always call this "
                "tool for the numbers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "enquiry_id": {
                        "type": "string",
                        "description": "The enquiry_id from a prior call. Omit on the first call.",
                    },
                    **_AFFORDABILITY_FIELD_SCHEMAS,
                },
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="start_mortgage_application",
            title="Start mortgage application",
            description=(
                "Begin the interactive mortgage / home loan application flow. "
                "Call this the moment the user says they want a mortgage or "
                "home loan. Returns an `application_id` (in structuredContent) "
                "plus the first question to ask the user — ask ONE question at "
                "a time, wait for the reply, then call `provide_mortgage_details` "
                "with that single field and the SAME `application_id`, repeating "
                "until all fields are collected."
            ),
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        types.Tool(
            name="provide_mortgage_details",
            title="Provide mortgage application details",
            description=(
                "Record one field of the mortgage application (whichever field "
                "the user just answered) and get the next question. Always pass "
                "the `application_id` returned by `start_mortgage_application`. "
                "Pass only the field(s) the user just gave you — do not guess "
                "values for fields not yet asked. The response tells you the "
                "next question to ask, or — once all fields are collected — an "
                "email/reference ID verification prompt (follow up with "
                "`submit_verification_email`). Every mortgage application "
                "must pass its own reference ID check before it can be submitted, so "
                "expect this step even if the user verified earlier in the "
                "chat. Render the returned text verbatim — never paraphrase it "
                "or claim a step (like 'reference ID sent') happened without actually "
                "calling the corresponding tool first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "application_id": {
                        "type": "string",
                        "description": "The application_id from start_mortgage_application.",
                    },
                    **_MORTGAGE_FIELD_SCHEMAS,
                    "value": {
                        "type": "number",
                        "description": (
                            "Fallback: the number the user just replied with, when "
                            "you're unsure which field it belongs to. It is applied "
                            "to whichever question was just asked."
                        ),
                    },
                },
                "required": ["application_id"],
                "additionalProperties": True,
            },
        ),
        types.Tool(
            name="submit_verification_email",
            title="Submit email for identity verification",
            description=(
                "Submit the user's email address to start identity "
                "verification. Call this after the user is asked to "
                "verify their identity (either at the end of the mortgage details flow, "
                "or when `list_transactions` asks for it) and replies with "
                "their email. Pass `application_id` only when this is part of "
                "the mortgage flow (omit it for the transactions flow); if it "
                "was `list_transactions` that asked for verification, pass "
                "the SAME `query` you gave it, so the answer the user "
                "originally asked for is what's shown once they verify. Sends "
                "a reference ID to that email and asks the user to enter it — "
                "then call `verify_reference_id`. If the email format is "
                "invalid, no reference ID is sent; ask the user for a valid email again "
                "and re-call this tool. IMPORTANT: you MUST actually call this "
                "tool to trigger the send — never tell the user a reference ID was sent "
                "without having called it, and always relay the exact text this "
                "tool returns rather than inventing your own confirmation message."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "The user's email address."},
                    "application_id": {
                        "type": "string",
                        "description": "Pass this only when verifying to finish a mortgage application.",
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Pass this only when verifying to finish a transactions "
                            "request — the same `query` you gave `list_transactions`."
                        ),
                    },
                },
                "required": ["email"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="verify_reference_id",
            title="Verify reference ID",
            description=(
                "Verify the reference ID the user received by email, "
                "following `submit_verification_email`. If the code matches: "
                "for the MORTGAGE flow, the result directly completes it — a "
                "mortgage application success card. For the TRANSACTIONS flow, "
                "the result only confirms verification in plain text — it does "
                "NOT show the transactions card. You MUST immediately follow up "
                "with one more call to `list_transactions` (same `query` as "
                "before, if there was one) to actually display the result; "
                "ChatGPT only renders the transactions card from a "
                "`list_transactions` call, never from `verify_reference_id`. "
                "If the code is wrong or expired, the request is CANCELLED — "
                "the result says 'Invalid reference ID' and the pending mortgage "
                "application is discarded. Do not retry the code in that case; "
                "relay the cancellation to the user and, if they want to "
                "continue, start over with `start_mortgage_application`. You "
                "MUST actually call this tool with the code the user typed — "
                "never assume it's correct and skip straight to a success "
                "message. "
                "For the mortgage success/cancellation card: OUTPUT THE RETURNED "
                "MARKDOWN VERBATIM AND IN FULL — do not summarise, shorten or "
                "reword it. It is a formatted banking card. Replying with "
                "something like 'Your application has been submitted' instead "
                "of the card drops the approved amount, interest rate, term, "
                "monthly payment and Application ID, all of which the customer "
                "needs. Likewise, when a code is rejected, show the card so the "
                "customer sees the reference ID was wrong."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "reference_id": {"type": "string", "description": "The 6-digit code the user entered."},
                },
                "required": ["reference_id"],
                "additionalProperties": False,
            },
        ),
    ]


async def _handle_call_tool(req: types.CallToolRequest) -> types.ServerResult:
    """Registered manually rather than via @server.call_tool().

    The decorator builds `CallToolResult(content=..., structuredContent=...)`
    with no `_meta`, which means the widget template is never referenced on
    the result and ChatGPT renders plain text instead of the component.
    """
    name = req.params.name
    arguments = req.params.arguments or {}

    try:
        if name == "list_transactions":
            result = _handle_list_transactions(arguments)
        elif name == "check_affordability":
            result = _handle_check_affordability(arguments)
        elif name == "start_mortgage_application":
            result = _handle_start_mortgage_application()
        elif name == "provide_mortgage_details":
            result = _handle_provide_mortgage_details(arguments)
        elif name == "submit_verification_email":
            result = _handle_submit_verification_email(arguments)
        elif name == "verify_reference_id":
            result = _handle_verify_reference_id(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    except Exception as exc:  # surfaced to the model rather than dropping the connection
        logger.exception("Tool %s failed", name)
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {exc}")],
            isError=True,
        )

    return types.ServerResult(result)


server.request_handlers[types.CallToolRequest] = _handle_call_tool


def _with_request_logging(handler):
    """Wrap an MCP request handler so every request/response cycle it
    dispatches — not just tool calls — is audit-logged (backend/request_log.py),
    pretty-printed and blank-line-framed. Applied to every entry in
    `server.request_handlers` below, once all of them are registered."""

    async def wrapped(req: Any) -> types.ServerResult:
        try:
            result = await handler(req)
        except Exception as exc:
            request_log.log_call(req, None, error=exc)
            raise
        request_log.log_call(req, result)
        return result

    return wrapped


for _req_type, _handler in list(server.request_handlers.items()):
    server.request_handlers[_req_type] = _with_request_logging(_handler)


def _text_result(text: str, structured: dict[str, Any] | None = None) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=False,
    )


def _run_transaction_query(query: str) -> types.CallToolResult:
    """Answer a transaction question with no LLM call — see
    backend/transactions_data.py. A plain listing renders as the widget
    card; every aggregation (sum/count/avg/min/max/balance/breakdown/top_n)
    is plain text with no card."""
    outcome = transactions_data.run_query(query)

    if outcome["mode"] == "widget":
        structured = {
            **outcome["structured"],
            "lloyds_web_app_url": f"{config.FRONTEND_BASE_URL}/transactions",
        }
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=outcome["text"])],
            structuredContent=structured,
            isError=False,
            **{"_meta": TRANSACTIONS_WIDGET_META},
        )

    return _text_result(outcome["text"])


def _handle_list_transactions(arguments: dict[str, Any]) -> types.CallToolResult:
    query = (arguments.get("query") or "").strip()
    if not auth.is_verified():
        return _text_result(render_email_prompt())
    return _run_transaction_query(query)


def _coerce_percentage(value: Any) -> float | None:
    """Parse a deposit percentage, tolerating '15%' formatting and a bare
    ratio (e.g. 0.15 meaning 15%) — same forgiving spirit as `_coerce_amount`."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        cleaned = value.strip().rstrip("%")
        try:
            value = float(cleaned)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if 0 < parsed < 1:
        parsed *= 100
    return parsed


def _validate_affordability_field(field: str, value: Any) -> tuple[Any, str | None]:
    if field == "deposit_percentage":
        parsed = _coerce_percentage(value)
        if parsed is None:
            return None, "That doesn't look like a valid percentage — could you try again?"
        if not (0 <= parsed < 100):
            return None, "Deposit percentage should be between 0 and 100."
        return parsed, None

    if field == "deposit_amount":
        parsed = _coerce_amount(value)
        if parsed is None:
            return None, "That doesn't look like a valid amount — could you try again?"
        if parsed < 0:
            return None, "Deposit amount can't be negative."
        return parsed, None

    parsed = _coerce_amount(value)
    if parsed is None:
        return None, "That doesn't look like a valid number — could you try again?"
    if parsed <= 0:
        return None, "Combined annual income needs to be a positive number."
    return parsed, None


def _handle_check_affordability(arguments: dict[str, Any]) -> types.CallToolResult:
    enquiry_id = arguments.get("enquiry_id")
    draft = store.get_affordability_draft(enquiry_id) if enquiry_id else None
    draft = draft or store.get_latest_affordability_draft()
    if draft is None:
        draft = store.create_affordability_draft()
    enquiry_id = draft["enquiry_id"]

    if "combined_annual_income" not in draft["fields"] and arguments.get("combined_annual_income") is not None:
        parsed_value, error = _validate_affordability_field(
            "combined_annual_income", arguments["combined_annual_income"]
        )
        if error:
            return _text_result(
                render_affordability_field_question("combined_annual_income", error=error),
                structured={"enquiry_id": enquiry_id},
            )
        store.update_affordability_draft(enquiry_id, combined_annual_income=parsed_value)

    # Deposit can arrive as either a percentage or a cash amount — capture
    # whichever the user gave (never both, and never converted from one to
    # the other here; the underlying calc handles that).
    has_deposit = "deposit_percentage" in draft["fields"] or "deposit_amount" in draft["fields"]
    if not has_deposit:
        for deposit_field in ("deposit_percentage", "deposit_amount"):
            if arguments.get(deposit_field) is None:
                continue
            parsed_value, error = _validate_affordability_field(deposit_field, arguments[deposit_field])
            if error:
                return _text_result(
                    render_affordability_field_question("deposit", error=error),
                    structured={"enquiry_id": enquiry_id},
                )
            store.update_affordability_draft(enquiry_id, **{deposit_field: parsed_value})
            break

    missing = []
    if "combined_annual_income" not in draft["fields"]:
        missing.append("combined_annual_income")
    if "deposit_percentage" not in draft["fields"] and "deposit_amount" not in draft["fields"]:
        missing.append("deposit")
    if missing:
        return _text_result(
            render_affordability_field_question(missing[0]),
            structured={"enquiry_id": enquiry_id},
        )

    fields = draft["fields"]
    try:
        result = bank_client.check_affordability(
            combined_annual_income=float(fields["combined_annual_income"]),
            deposit_percentage=float(fields["deposit_percentage"]) if "deposit_percentage" in fields else None,
            deposit_amount=float(fields["deposit_amount"]) if "deposit_amount" in fields else None,
        )
    except bank_client.BankUnavailable as exc:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(exc))],
            isError=True,
        )

    store.discard_affordability_draft(enquiry_id)

    # fields shown on the card — the enquiry is finished (its draft is
    # discarded above); total_interest/total_amount_repaid/is_indicative
    # are derivable but not part of what we show.
    return _text_result(
        render_affordability_card(result),
        structured={
            "property_price": result["property_price"],
            "deposit_amount": result["deposit_amount"],
            "deposit_percentage": result["deposit_percentage"],
            "loan_amount": result["loan_amount"],
            "interest_rate": result["interest_rate"],
            "term_years": result["term_years"],
            "monthly_payment": result["monthly_payment"],
        },
    )


def _handle_start_mortgage_application() -> types.CallToolResult:
    draft = store.create_draft()
    application_id = draft["application_id"]
    first_field = MORTGAGE_FIELDS[0]
    bank_client.log_chat(application_id, "Lloyds", "Mortgage application started.")
    bank_client.log_chat(
        application_id, "Lloyds", MORTGAGE_FIELD_QUESTIONS[first_field]
    )
    return _text_result(
        render_mortgage_field_question(first_field),
        structured={"application_id": application_id},
    )


def _coerce_amount(value: Any) -> float | None:
    """Parse a user-supplied amount, tolerating '£55,000' style formatting.

    Users type currency the way they say it, and the model passes it straight
    through, so normalise here rather than rejecting it as non-numeric.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    for junk in ("£", "$", "€", ",", "_", " "):
        cleaned = cleaned.replace(junk, "")
    try:
        return float(cleaned)
    except ValueError:
        return None


# Plausibility floors for the money questions. Without these a term answered
# against the property-value question ("20") is accepted as a £20 property,
# which sails through and produces a nonsensical loan-to-value.
_FIELD_MINIMUMS: dict[str, tuple[float, str]] = {
    "loan_amount": (1_000, "That looks too small for a mortgage — how much would you like to borrow?"),
    "property_value": (
        10_000,
        "That looks too small for a property price. What is the property worth, in GBP?",
    ),
}


def _validate_mortgage_field(field: str, value: Any) -> tuple[Any, str | None]:
    parsed = _coerce_amount(value)
    if parsed is None:
        return None, "That doesn't look like a valid number — could you try again?"

    if field == "preferred_term":
        term = int(parsed)
        if not (5 <= term <= 35):
            return None, "The mortgage term needs to be between 5 and 35 years."
        return term, None

    if parsed <= 0:
        return None, f"{MORTGAGE_FIELD_QUESTIONS[field]} needs to be a positive number."

    minimum, message = _FIELD_MINIMUMS.get(field, (0.0, ""))
    if parsed < minimum:
        return None, message

    return parsed, None


# Models don't reliably use our exact parameter names — they paraphrase
# ("salary"), or a client caching an older tool schema sends names we no longer
# publish. Any of these map onto the canonical field.
_FIELD_ALIASES: dict[str, str] = {
    "loan_amount": "loan_amount",
    "loan": "loan_amount",
    "loan_value": "loan_amount",
    "borrow_amount": "loan_amount",
    "amount_to_borrow": "loan_amount",
    "requested_loan": "loan_amount",
    "mortgage_amount": "loan_amount",
    "property_value": "property_value",
    "property_price": "property_value",
    "purchase_price": "property_value",
    "house_price": "property_value",
    "home_value": "property_value",
    "property": "property_value",
    "value_of_property": "property_value",
    "preferred_term": "preferred_term",
    "term": "preferred_term",
    "term_years": "preferred_term",
    "years": "preferred_term",
    "mortgage_term": "preferred_term",
    "duration": "preferred_term",
    "repayment_period": "preferred_term",
}

# Never treated as an answer value.
_NON_FIELD_ARGS = {"application_id"}


def _resolve_submitted_value(arguments: dict[str, Any], target_field: str) -> Any:
    """Find the value the user just supplied for `target_field`.

    Checks the canonical name, then known aliases, then falls back to any other
    numeric argument. The fallback matters: whatever key the model chose, the
    number it just sent is the answer to the question we just asked, and
    dropping it silently would re-ask the same question forever.
    """
    if arguments.get(target_field) is not None:
        return arguments[target_field]

    for key, value in arguments.items():
        if value is None or key in _NON_FIELD_ARGS:
            continue
        if _FIELD_ALIASES.get(key) == target_field:
            return value

    for key, value in arguments.items():
        if value is None or key in _NON_FIELD_ARGS:
            continue
        # Don't steal a value that clearly belongs to the other field.
        mapped = _FIELD_ALIASES.get(key)
        if mapped and mapped != target_field:
            continue
        if _coerce_amount(value) is not None:
            return value
    return None


def _handle_provide_mortgage_details(arguments: dict[str, Any]) -> types.CallToolResult:
    application_id = arguments.get("application_id")
    draft = store.get_draft(application_id) if application_id else None
    draft = draft or store.get_latest_draft()
    if draft is None:
        raise ValueError("No mortgage application in progress — call start_mortgage_application first.")

    application_id = draft["application_id"]

    # Apply explicitly-named fields first, so a call carrying several answers
    # at once lands each value in the right slot.
    applied_named = False
    for field in MORTGAGE_FIELDS:
        if field in draft["fields"] or arguments.get(field) is None:
            continue
        parsed_value, error = _validate_mortgage_field(field, arguments[field])
        if error:
            return _text_result(
                render_mortgage_field_question(field, error=error),
                structured={"application_id": application_id},
            )
        store.update_draft(application_id, **{field: parsed_value})
        applied_named = True

    # Only when nothing was named do we fall back to interpreting a stray
    # numeric as the answer to the question just asked. Doing it after a named
    # field has already landed would let a redundant argument — models like to
    # echo the same number under a generic key — silently answer the *next*
    # question too, skipping it entirely.
    missing = [f for f in MORTGAGE_FIELDS if f not in draft["fields"]]
    if missing and not applied_named:
        target = missing[0]
        submitted = _resolve_submitted_value(arguments, target)
        if submitted is not None:
            parsed_value, error = _validate_mortgage_field(target, submitted)
            if error:
                return _text_result(
                    render_mortgage_field_question(target, error=error),
                    structured={"application_id": application_id},
                )
            store.update_draft(application_id, **{target: parsed_value})
            missing = [f for f in MORTGAGE_FIELDS if f not in draft["fields"]]

    # Catch a property worth less than the loan here rather than letting it
    # reach underwriting as an absurd loan-to-value.
    fields = draft["fields"]
    if "loan_amount" in fields and "property_value" in fields:
        if float(fields["property_value"]) < float(fields["loan_amount"]):
            store.update_draft_remove(application_id, "property_value")
            return _text_result(
                render_mortgage_field_question(
                    "property_value",
                    error=(
                        f"The property needs to be worth at least what you're borrowing "
                        f"(£{float(fields['loan_amount']):,.0f})."
                    ),
                ),
                structured={"application_id": application_id},
            )

    if missing:
        question = MORTGAGE_FIELD_QUESTIONS[missing[0]]
        bank_client.log_chat(application_id, "Customer", str(_last_answer(arguments)))
        bank_client.log_chat(application_id, "Lloyds", question)
        return _text_result(
            render_mortgage_field_question(missing[0]),
            structured={"application_id": application_id},
        )

    bank_client.log_chat(application_id, "Customer", str(_last_answer(arguments)))

    if auth.is_application_verified(application_id):
        return _finalize_mortgage_application(application_id)

    bank_client.log_chat(
        application_id, "Lloyds", "Please confirm your email address to continue."
    )
    return _text_result(render_email_prompt(), structured={"application_id": application_id})


def _last_answer(arguments: dict[str, Any]) -> Any:
    """The value the customer just supplied, for the bank's chat mirror."""
    for key, value in arguments.items():
        if key not in _NON_FIELD_ARGS and value is not None:
            return value
    return ""


def _offer_for_web_app(
    application_id: str, result: dict[str, Any], draft_fields: dict[str, Any]
) -> dict[str, Any]:
    """Map the bank's underwriting result into the shape the Lloyds web app and
    `store.save_application()` already expect."""
    offer = result["offer"]
    customer = result["customer"]
    checks = result["checks"]

    return {
        "annual_salary": customer["applicant_annual_income"],
        "property_value": draft_fields.get("property_value"),
        "deposit_amount": customer["deposit_amount"],
        "requested_loan_amount": draft_fields.get("loan_amount"),
        "loan_amount": offer["amount"],
        "interest_rate": offer["rate"],
        "term_years": offer["term"],
        "estimated_monthly_payment": offer["monthly"],
        "total_repayable": round(offer["monthly"] * offer["term"] * 12, 2),
        "total_interest": round(offer["monthly"] * offer["term"] * 12 - offer["amount"], 2),
        "application_status": "Approved" if result["approved"] else "Declined",
        "application_id": application_id,
        "customer_name": customer["full_name"],
        "credit_score": customer["credit_score"],
        "ltv": checks.get("ltv", {}).get("ltv"),
        "dti_percent": checks.get("dti", {}).get("dti_percent"),
        "eligibility": {
            "eligible": result["approved"],
            "loan_to_income_multiple": 4.5,
            "notes": result["rationale"],
        },
        "next_steps": [
            "Confirm your identity and proof of income",
            "Submit a property valuation request",
            "Speak with a Lloyds mortgage advisor",
            "Receive your formal Decision in Principle",
        ],
        "generated_at": datetime.now().isoformat(),
    }


def _finalize_mortgage_application(application_id: str) -> types.CallToolResult:
    """Hand the application to the bank for underwriting and render the result."""
    draft = store.get_draft(application_id)
    if draft is None:
        raise ValueError("Mortgage application draft not found — please start again.")

    # Ask for anything still outstanding rather than blowing up on a missing
    # key. The application stays verified, so answering the question finishes
    # the submission without a second round of identity checks.
    missing = [f for f in MORTGAGE_FIELDS if f not in draft["fields"]]
    if missing:
        return _text_result(
            render_mortgage_field_question(missing[0]),
            structured={"application_id": application_id},
        )

    email = auth.verified_email()
    if not email:
        return _text_result(render_email_prompt())

    try:
        result = bank_client.underwrite(
            session_id=application_id,
            email=email,
            loan_amount=float(draft["fields"]["loan_amount"]),
            property_value=float(draft["fields"]["property_value"]),
            preferred_term=int(draft["fields"]["preferred_term"]),
        )
    except bank_client.BankUnavailable as exc:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(exc))],
            isError=True,
        )

    store.discard_draft(application_id)
    auth.discard_application_verification(application_id)

    if not result["approved"]:
        bank_client.log_chat(application_id, "Lloyds", result["rationale"])
        return _text_result(render_declined_card(application_id, result))

    offer = _offer_for_web_app(application_id, result, draft["fields"])
    store.save_application(offer)

    bank_client.log_chat(
        application_id,
        "Lloyds",
        f"Approved — £{offer['loan_amount']:,.2f} over {offer['term_years']} years "
        f"at {offer['interest_rate']}%.",
    )

    web_app_url = f"{config.FRONTEND_BASE_URL}/mortgage?application_id={application_id}"
    image_url = f"{config.FRONTEND_BASE_URL}/img/mortgage-success.svg"
    # Also surface the figures as structured data, so the approved terms
    # survive even if the assistant paraphrases the card instead of rendering it.
    return _text_result(
        render_mortgage_success_card(offer, web_app_url, image_url),
        structured={
            "application_id": application_id,
            "status": "approved",
            "customer_name": offer.get("customer_name"),
            "loan_amount": offer["loan_amount"],
            "interest_rate": offer["interest_rate"],
            "term_years": offer["term_years"],
            "monthly_payment": offer["estimated_monthly_payment"],
            "ltv": offer.get("ltv"),
            "credit_score": offer.get("credit_score"),
            "relationship_manager": "Our Relationship Manager will contact you shortly.",
        },
    )


def _handle_submit_verification_email(arguments: dict[str, Any]) -> types.CallToolResult:
    email = arguments.get("email", "")
    application_id = arguments.get("application_id")
    query = arguments.get("query")
    purpose = "mortgage" if application_id else "transactions"

    if not auth.is_valid_email(email):
        return _text_result(
            render_email_prompt(error="That doesn't look like a valid email address.")
        )

    # Resolve the customer before sending anything. The email identifies a real
    # account here, so an unknown address is a dead end — no point issuing a code.
    try:
        customer = bank_client.get_customer(email)
    except bank_client.BankUnavailable as exc:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(exc))],
            isError=True,
        )

    if customer is None:
        return _text_result(
            render_email_prompt(
                error=f"We couldn't find a Lloyds account for {email.strip()}."
            )
        )

    if application_id:
        bank_client.log_chat(application_id, "Customer", email.strip())
        bank_client.log_chat(
            application_id,
            "Lloyds",
            f"Matched customer {customer['customer_id']} — sending a verification code.",
        )

    try:
        auth.start_verification(email, purpose, application_id, query=query)
    except ValueError as exc:
        return _text_result(render_email_prompt(error=str(exc)))
    except RuntimeError as exc:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"We couldn't send your verification code: {exc}")],
            isError=True,
        )

    return _text_result(render_reference_id_prompt())


def _handle_verify_reference_id(arguments: dict[str, Any]) -> types.CallToolResult:
    reference_id = arguments.get("reference_id", "")

    if not auth.verify(reference_id):
        # A wrong (or expired) code aborts the whole request rather than
        # looping: the pending session is torn down so the issued code can't
        # be retried, and any half-built application is thrown away.
        purpose, application_id = auth.cancel_pending()
        if purpose == "mortgage" and application_id:
            store.discard_draft(application_id)
            auth.discard_application_verification(application_id)
            logger.info("Reference ID rejected — cancelled mortgage application %s", application_id)
            return _text_result(render_reference_id_cancelled(application_id))
        logger.info("Reference ID rejected — cancelled %s request", purpose or "pending")
        return _text_result(render_reference_id_cancelled())

    purpose, application_id, query = auth.pop_pending()
    if purpose == "mortgage" and application_id:
        return _finalize_mortgage_application(application_id)
    if purpose == "transactions":
        # Deliberately NOT rendering the widget from here: ChatGPT only ever
        # mounts the inline UI component for a tool that *itself* declares
        # `openai/outputTemplate` in tools/list (see TRANSACTIONS_WIDGET_META
        # on the `list_transactions` Tool definition) — `verify_reference_id`
        # declares no such template, so attaching widget `_meta` to *its*
        # result renders nothing (the earlier `list_transactions` call's own
        # optimistic widget placeholder is left stuck on "Loading…" instead).
        # The fix is to hand back to the model with an explicit instruction
        # to re-call `list_transactions`, the only tool ChatGPT will actually
        # render a fresh card for.
        text = "✅ You're verified."
        return _text_result(
            f"{text} Call `list_transactions` now"
            + (f" with query=\"{query}\"" if query else "")
            + " to show the result."
        )

    return _text_result("✅ You're verified.")


# --- Streamable HTTP transport ------------------------------------------------
# Stateless keeps each request self-contained, which is more robust behind an
# ngrok tunnel than session-affinity-dependent stateful mode.
session_manager = StreamableHTTPSessionManager(app=server, json_response=False, stateless=True)


class MCPASGIApp:
    """Raw ASGI wrapper so the endpoint can be mounted at exactly `/mcp`
    (a Starlette Mount would 307-redirect `/mcp` -> `/mcp/`)."""

    async def __call__(self, scope, receive, send) -> None:
        await session_manager.handle_request(scope, receive, send)


mcp_asgi_app = MCPASGIApp()
