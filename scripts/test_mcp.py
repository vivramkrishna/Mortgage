#!/usr/bin/env python3
"""Verify the MCP server speaks the ChatGPT Apps SDK widget protocol correctly,
and walk the mortgage + reference ID verification + transactions journey end to end.

Connects as a real MCP client over Streamable HTTP and asserts that:
  * `list_transactions` advertises `_meta["openai/outputTemplate"]`
  * the referenced resource exists and is served with an MCP Apps UI MIME type
    (`text/html;profile=mcp-app`, or legacy `text/html+skybridge`)
  * calling the tool returns `structuredContent` (what the widget renders from)
  * transactions are gated behind email + reference ID verification
  * the mortgage journey collects fields one at a time, then requires the same
    email + reference ID flow before submitting
  * once verified, that state is reused — transactions no longer prompts

Reference IDs are read back via the local-dev-only `/api/_debug/reference-id` endpoint
(`DEBUG_EXPOSE_REFERENCE_ID=true` in `.env`), since verifying real inbox delivery isn't
automatable here.

Usage:
    python scripts/test_mcp.py [base_url]      # default http://localhost:8000
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

TRANSACTIONS_WIDGET_URI = "ui://widget/transactions.html"
VALID_UI_MIMES = {"text/html;profile=mcp-app", "text/html+skybridge"}

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

MORTGAGE_ANSWERS = {
    "loan_amount": 250_000,
    "property_value": 400_000,
    "preferred_term": 25,
}

# Resolved by the bank from the email the customer verifies with.
KNOWN_CUSTOMER_EMAIL = "konavivekramakrishna@gmail.com"
UNKNOWN_CUSTOMER_EMAIL = "nobody.at.this.bank@example.com"

BANK_BASE_URL = os.getenv("BANK_BASE_URL", "http://localhost:4000")


class Checker:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{PASS if ok else FAIL}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures += 1

    def skip(self, label: str, reason: str) -> None:
        print(f"  [{SKIP}] {label} — {reason}")


def text_of(result) -> str:
    return result.content[0].text if result.content else ""


async def reset_backend_state(base_url: str) -> bool:
    """Clear verification/application state so the suite is re-runnable against a
    long-lived backend. Needs DEBUG_EXPOSE_REFERENCE_ID=true."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{base_url}/api/_debug/reset", timeout=5)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200


async def fetch_bank_session(session_id: str) -> dict | None:
    """Read a session back from the bank core, to prove it recorded what the
    Command Centre renders."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{BANK_BASE_URL}/api/sessions/{session_id}", timeout=5)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        return resp.json()


async def fetch_debug_reference_id(base_url: str) -> str | None:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{base_url}/api/_debug/reference-id", timeout=5)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        return resp.json().get("reference_id")


async def main(base_url: str) -> int:
    url = base_url.rstrip("/") + "/mcp"
    c = Checker()
    print(f"\nConnecting to {url}\n")

    # A previous run leaves the session verified for an hour, which would make
    # the "unverified" assertions below fail on a re-run.
    if not await reset_backend_state(base_url):
        print("  note: could not reset backend state (needs DEBUG_EXPOSE_REFERENCE_ID=true);")
        print("        the unverified checks may fail on a repeat run.\n")

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print(f"Server: {info.serverInfo.name} (protocol {info.protocolVersion})\n")

            # --- tools ---
            print("tools/list")
            tools = {t.name: t for t in (await session.list_tools()).tools}
            expected_tools = {
                "list_transactions",
                "start_mortgage_application",
                "provide_mortgage_details",
                "submit_verification_email",
                "verify_reference_id",
            }
            c.check("all five tools present", set(tools) == expected_tools, ", ".join(sorted(tools)))

            txn = tools.get("list_transactions")
            meta = (txn.meta or {}) if txn else {}
            c.check("list_transactions declares ui.resourceUri (MCP Apps standard)",
                    (meta.get("ui") or {}).get("resourceUri") == TRANSACTIONS_WIDGET_URI,
                    str((meta.get("ui") or {}).get("resourceUri")))
            c.check("list_transactions declares openai/outputTemplate (ChatGPT alias)",
                    meta.get("openai/outputTemplate") == TRANSACTIONS_WIDGET_URI,
                    str(meta.get("openai/outputTemplate")))
            c.check("list_transactions has invoking/invoked labels",
                    bool(meta.get("openai/toolInvocation/invoking")
                         and meta.get("openai/toolInvocation/invoked")))

            for name in ("start_mortgage_application", "provide_mortgage_details",
                         "submit_verification_email", "verify_reference_id"):
                t = tools.get(name)
                has_tpl = bool((t.meta or {}).get("openai/outputTemplate")) if t else False
                c.check(f"{name} has NO widget (as intended)", not has_tpl)

            # --- resources ---
            print("\nresources/list + resources/read")
            resources = (await session.list_resources()).resources
            widget = next((r for r in resources if str(r.uri) == TRANSACTIONS_WIDGET_URI), None)
            c.check("widget resource is listed", widget is not None)
            if widget:
                c.check("resource mimeType is an MCP Apps UI type",
                        widget.mimeType in VALID_UI_MIMES, str(widget.mimeType))
                rmeta_ui = (widget.meta or {}).get("ui") or {}
                c.check("resource _meta.ui declares csp", "csp" in rmeta_ui,
                        str(sorted(rmeta_ui)))

            read = await session.read_resource(TRANSACTIONS_WIDGET_URI)  # type: ignore[arg-type]
            contents = read.contents[0]
            html = getattr(contents, "text", "") or ""
            c.check("resource read returns an MCP Apps UI mimeType",
                    contents.mimeType in VALID_UI_MIMES, str(contents.mimeType))
            c.check("widget HTML is non-empty", len(html) > 500, f"{len(html)} bytes")
            c.check("widget uses documented <div id=\"root\"> + module script",
                    'id="root"' in html and 'type="module"' in html)
            c.check("widget handles ui/notifications/tool-result (MCP Apps)",
                    "ui/notifications/tool-result" in html)
            c.check("widget handles window.openai.toolOutput (ChatGPT compat)",
                    "window.openai" in html and "toolOutput" in html)
            c.check("widget is self-contained (no external URLs)",
                    "http://" not in html and "https://" not in html)

            # --- transactions require verification ---
            print("\ntools/call list_transactions (unverified)")
            gated = await session.call_tool("list_transactions", {})
            gtext = text_of(gated)
            c.check("unverified transactions asks for email",
                    "email id to continue" in gtext.lower())
            c.check("unverified transactions has NO widget meta",
                    not (gated.meta or {}).get("openai/outputTemplate"))

            # --- mortgage flow: one field at a time ---
            print("\ntools/call mortgage flow")
            start = await session.call_tool("start_mortgage_application", {})
            c.check("start asks how much to borrow",
                    "borrow" in text_of(start).lower())
            c.check("mortgage start result has NO widget meta",
                    not (start.meta or {}).get("openai/outputTemplate"))
            app_id = (start.structuredContent or {}).get("application_id")
            c.check("start returns an application_id", bool(app_id), str(app_id))

            fields = list(MORTGAGE_ANSWERS.items())
            last_result = None
            for field, value in fields:
                last_result = await session.call_tool(
                    "provide_mortgage_details", {"application_id": app_id, field: value}
                )
            final_text = text_of(last_result)
            c.check("after last field, mortgage flow asks for email",
                    "email id to continue" in final_text.lower(), final_text[:120])

            # --- the answer must land whatever key the model used ---
            # Regression guard: an unrecognised/aliased parameter name used to be
            # silently dropped, re-asking the same question forever.
            print("\ntools/call provide_mortgage_details (aliased / unknown keys)")
            for label, payload in [
                ("alias 'term'", {"term": 25}),
                ("alias 'term_years'", {"term_years": 25}),
                ("generic 'value'", {"value": 25}),
                ("unknown key 'foo'", {"foo": 25}),
                ("string '25'", {"term": "25"}),
            ]:
                alias_start = await session.call_tool("start_mortgage_application", {})
                alias_id = (alias_start.structuredContent or {}).get("application_id")
                await session.call_tool(
                    "provide_mortgage_details", {"application_id": alias_id, "loan_amount": 250_000}
                )
                await session.call_tool(
                    "provide_mortgage_details", {"application_id": alias_id, "property_value": 400_000}
                )
                res = await session.call_tool(
                    "provide_mortgage_details", {"application_id": alias_id, **payload}
                )
                moved_on = "email id to continue" in text_of(res).lower()
                c.check(f"{label} advances past the term question", moved_on,
                        "" if moved_on else text_of(res)[:80])

            # Nonsense answers must be caught at the question, not carried into
            # underwriting as an absurd loan-to-value.
            print("\ntools/call provide_mortgage_details (implausible values)")
            sanity_start = await session.call_tool("start_mortgage_application", {})
            sanity_id = (sanity_start.structuredContent or {}).get("application_id")
            await session.call_tool(
                "provide_mortgage_details", {"application_id": sanity_id, "loan_amount": 250_000}
            )
            tiny = await session.call_tool(
                "provide_mortgage_details", {"application_id": sanity_id, "property_value": 20}
            )
            c.check("a £20 property value is rejected",
                    "too small" in text_of(tiny).lower(), text_of(tiny)[:90])
            under = await session.call_tool(
                "provide_mortgage_details", {"application_id": sanity_id, "property_value": 100_000}
            )
            c.check("a property worth less than the loan is rejected",
                    "at least what you" in text_of(under).lower(), text_of(under)[:90])
            ok = await session.call_tool(
                "provide_mortgage_details", {"application_id": sanity_id, "property_value": 400_000}
            )
            c.check("a sensible property value is then accepted",
                    "repay it" in text_of(ok).lower(), text_of(ok)[:90])

            # A redundant numeric alongside a named field must NOT be taken as
            # the answer to the *next* question — that silently skipped the
            # property-value step entirely.
            print("\ntools/call provide_mortgage_details (no question may be skipped)")
            for label, payload in [
                ("named field + generic 'value'", {"loan_amount": 432_234, "value": 432_234}),
                ("named field + stray 'amount'", {"loan_amount": 432_234, "amount": 432_234}),
                ("named field + unrelated number", {"loan_amount": 432_234, "confidence": 1}),
            ]:
                skip_start = await session.call_tool("start_mortgage_application", {})
                skip_id = (skip_start.structuredContent or {}).get("application_id")
                res = await session.call_tool(
                    "provide_mortgage_details", {"application_id": skip_id, **payload}
                )
                asked_property = "property" in text_of(res).lower()
                c.check(f"{label} still asks for the property value", asked_property,
                        "" if asked_property else text_of(res)[:90])

            # A client still holding an older cached tool schema cannot send
            # `property_value` at all. Answering every step through the generic
            # `value` parameter must still walk the whole flow in order.
            print("\ntools/call provide_mortgage_details (stale cached schema)")
            stale_start = await session.call_tool("start_mortgage_application", {})
            stale_id = (stale_start.structuredContent or {}).get("application_id")
            c.check("stale: first question is the loan amount",
                    "borrow" in text_of(stale_start).lower())
            r1 = await session.call_tool(
                "provide_mortgage_details", {"application_id": stale_id, "value": 250_000}
            )
            c.check("stale: second question is the property value",
                    "property" in text_of(r1).lower(), text_of(r1)[:80])
            r2 = await session.call_tool(
                "provide_mortgage_details", {"application_id": stale_id, "value": 400_000}
            )
            c.check("stale: third question is the term",
                    "repay it" in text_of(r2).lower(), text_of(r2)[:80])
            r3 = await session.call_tool(
                "provide_mortgage_details", {"application_id": stale_id, "value": 25}
            )
            c.check("stale: flow reaches identity verification",
                    "email id to continue" in text_of(r3).lower(), text_of(r3)[:80])

            # The same resolver must land the property value in the right slot.
            print("\ntools/call provide_mortgage_details (property value aliases)")
            for label, payload in [
                ("alias 'purchase_price'", {"purchase_price": 400_000}),
                ("alias 'property_price'", {"property_price": 400_000}),
                ("string with £ and comma", {"property_value": "£400,000"}),
            ]:
                pv_start = await session.call_tool("start_mortgage_application", {})
                pv_id = (pv_start.structuredContent or {}).get("application_id")
                await session.call_tool(
                    "provide_mortgage_details", {"application_id": pv_id, "loan_amount": 250_000}
                )
                res = await session.call_tool(
                    "provide_mortgage_details", {"application_id": pv_id, **payload}
                )
                moved_on = "repay it" in text_of(res).lower()
                c.check(f"{label} advances to the term question", moved_on,
                        "" if moved_on else text_of(res)[:80])

            # --- email must resolve to a real customer ---
            print("\ntools/call submit_verification_email")
            bad_email = await session.call_tool(
                "submit_verification_email", {"email": "not-an-email", "application_id": app_id}
            )
            c.check("invalid email format is rejected without sending a reference ID",
                    "valid email" in text_of(bad_email).lower())

            unknown = await session.call_tool(
                "submit_verification_email",
                {"email": UNKNOWN_CUSTOMER_EMAIL, "application_id": app_id},
            )
            unknown_text = text_of(unknown)
            c.check("unknown customer is rejected before any code is sent",
                    "couldn't find" in unknown_text.lower(), unknown_text[:120])

            good_email = await session.call_tool(
                "submit_verification_email",
                {"email": KNOWN_CUSTOMER_EMAIL, "application_id": app_id},
            )
            email_text = text_of(good_email)
            if good_email.isError:
                c.skip("reference ID email send", f"SMTP not configured/reachable: {email_text}")
            else:
                c.check("valid email asks for the reference ID",
                        "reference id to continue" in email_text.lower())

            reference_id = await fetch_debug_reference_id(base_url)
            if reference_id is None:
                c.skip("reference ID verification checks", "DEBUG_EXPOSE_REFERENCE_ID is not enabled — set it in .env to run these")
            else:
                # --- wrong reference ID cancels this application outright ---
                wrong = "000000" if reference_id != "000000" else "111111"
                wrong_result = await session.call_tool("verify_reference_id", {"reference_id": wrong})
                wrong_text = text_of(wrong_result)
                c.check("wrong reference ID shows 'Invalid reference ID'",
                        "invalid reference id" in wrong_text.lower())
                c.check("wrong reference ID says plainly that the code is wrong",
                        "the code you entered is wrong" in wrong_text.lower(),
                        wrong_text.splitlines()[0][:90])
                c.check("wrong reference ID cancels the application",
                        "cancelled" in wrong_text.lower(), wrong_text[:120])

                # The issued code is dead even though it was the correct one.
                reused = await session.call_tool("verify_reference_id", {"reference_id": reference_id})
                c.check("the original code is void after cancellation",
                        "invalid reference id" in text_of(reused).lower())

                # --- a fresh application, verified correctly, submits ---
                print("\ntools/call mortgage flow (fresh application, correct reference ID)")
                start2 = await session.call_tool("start_mortgage_application", {})
                app_id2 = (start2.structuredContent or {}).get("application_id")
                c.check("cancelled application is not reused",
                        app_id2 != app_id, f"{app_id} -> {app_id2}")
                for field, value in fields:
                    await session.call_tool(
                        "provide_mortgage_details", {"application_id": app_id2, field: value}
                    )
                await session.call_tool(
                    "submit_verification_email",
                    {"email": KNOWN_CUSTOMER_EMAIL, "application_id": app_id2},
                )
                reference_id2 = await fetch_debug_reference_id(base_url)
                right_result = await session.call_tool("verify_reference_id", {"reference_id": reference_id2})
                right_text = text_of(right_result)
                c.check("correct reference ID submits the mortgage application",
                        "mortgage application submitted" in right_text.lower(),
                        right_text.splitlines()[0][:90])
                c.check("success card shows the Application ID",
                        f"`{app_id2}`" in right_text or app_id2 in right_text)
                c.check("success card mentions the Relationship Manager",
                        "relationship manager" in right_text.lower())
                c.check("success card embeds the placeholder image",
                        "mortgage-success.svg" in right_text)

                # The offer must come from the bank's underwriting, not a
                # hardcoded rate — so it carries the customer's real figures.
                c.check("offer is priced from the bank's underwriting",
                        "underwriting assessment" in right_text.lower(),
                        right_text[:160])
                c.check("success card greets the resolved customer by name",
                        "vivek" in right_text.lower())

                # The approved terms must be in the opening lines, and in
                # structuredContent, so they survive an assistant that
                # summarises the card instead of rendering it.
                lead = "\n".join(right_text.splitlines()[:8]).lower()
                for label, needle in [
                    ("approved amount", "£250,000.00"),
                    ("interest rate", "% apr"),
                    ("term", "25 years"),
                    ("monthly payment", "per month"),
                    ("relationship manager", "relationship manager"),
                ]:
                    c.check(f"success card leads with the {label}", needle in lead,
                            "" if needle in lead else lead[:100])

                sc = right_result.structuredContent or {}
                for key in ("loan_amount", "interest_rate", "term_years",
                            "monthly_payment", "application_id"):
                    c.check(f"structuredContent.{key} present for the offer", key in sc)

                # --- the bank recorded the session for the Command Centre ---
                print("\nbank core: session recorded")
                bank_record = await fetch_bank_session(app_id2)
                if bank_record is None:
                    c.skip("bank session checks", "bank core not reachable on :4000")
                else:
                    c.check("bank has a record for this application", True)
                    c.check("bank resolved the customer",
                            (bank_record.get("customer") or {}).get("email") == KNOWN_CUSTOMER_EMAIL,
                            str((bank_record.get("customer") or {}).get("email")))
                    c.check("bank ran all seven underwriting checks",
                            len(bank_record.get("checks") or {}) == 7,
                            str(len(bank_record.get("checks") or {})))
                    c.check("bank decision is approved",
                            (bank_record.get("decision") or {}).get("approved") is True)
                    c.check("bank recorded the live chat feed",
                            len(bank_record.get("chat") or []) > 0,
                            f"{len(bank_record.get('chat') or [])} messages")

                    # LTV must be computed against the property the customer
                    # typed, not whatever the bank had on file (£520,000 for
                    # this customer, which would give 48.08% instead).
                    expected_ltv = round(
                        MORTGAGE_ANSWERS["loan_amount"] / MORTGAGE_ANSWERS["property_value"] * 100, 2
                    )
                    actual_ltv = (bank_record.get("checks") or {}).get("ltv", {}).get("ltv")
                    c.check("LTV uses the customer-supplied property value",
                            actual_ltv == expected_ltv,
                            f"expected {expected_ltv}%, got {actual_ltv}%")
                    done_steps = [
                        s["name"] for s in bank_record["goal"]["steps"] if s["state"] == "done"
                    ]
                    c.check("goal plan advanced through PRESENT_OFFER",
                            "PRESENT_OFFER" in done_steps, ", ".join(done_steps))

                # --- verification is now reused: transactions no longer prompts ---
                print("\ntools/call list_transactions (now verified — reused session)")
                result = await session.call_tool("list_transactions", {})
                rmeta = result.meta or {}
                c.check("RESULT carries ui.resourceUri (renders the widget)",
                        (rmeta.get("ui") or {}).get("resourceUri") == TRANSACTIONS_WIDGET_URI,
                        str((rmeta.get("ui") or {}).get("resourceUri")))
                c.check("RESULT carries openai/outputTemplate (ChatGPT alias)",
                        rmeta.get("openai/outputTemplate") == TRANSACTIONS_WIDGET_URI,
                        str(rmeta.get("openai/outputTemplate")))

                sc = result.structuredContent
                c.check("returns structuredContent", isinstance(sc, dict))
                if isinstance(sc, dict):
                    for key in ("transactions", "total_spending", "transaction_count",
                                "average_transaction", "latest_transaction",
                                "spending_by_category", "lloyds_web_app_url"):
                        c.check(f"structuredContent.{key}", key in sc)
                    n = len(sc.get("transactions", []))
                    c.check("a non-empty, capped page of transactions is returned",
                            0 < n <= 30, str(n))
                    c.check("web app url points at /transactions",
                            str(sc.get("lloyds_web_app_url", "")).endswith("/transactions"),
                            str(sc.get("lloyds_web_app_url")))
                text = text_of(result)
                c.check("model-facing text is a short summary (not a full table)",
                        0 < len(text) < 300, f"{len(text)} chars")

                # --- dynamic queries: aggregations answer as text, no widget ---
                print("\ntools/call list_transactions (dynamic queries, no LLM)")
                for query, needle in [
                    ("how much did I spend on groceries", "£"),
                    ("how many transactions do I have", "transaction"),
                    ("what is my average transaction", "average"),
                    ("what was my cheapest transaction", "smallest"),
                    ("top 5 merchants", "top results"),
                ]:
                    agg = await session.call_tool("list_transactions", {"query": query})
                    agg_meta = agg.meta or {}
                    c.check(f"'{query}' has NO widget meta",
                            not agg_meta.get("openai/outputTemplate"))
                    c.check(f"'{query}' answers in text",
                            needle in text_of(agg).lower(), text_of(agg)[:100])

                for query in ("show my shopping transactions", "transactions in august 2025"):
                    lst = await session.call_tool("list_transactions", {"query": query})
                    lst_meta = lst.meta or {}
                    c.check(f"'{query}' renders the widget",
                            lst_meta.get("openai/outputTemplate") == TRANSACTIONS_WIDGET_URI)

                # --- regression: verify_reference_id must NOT try to render the
                # widget itself for the transactions flow (ChatGPT only mounts
                # the card for a tool that declares the output template in
                # tools/list, i.e. list_transactions — attaching widget _meta to
                # verify_reference_id's own result renders nothing in the real
                # ChatGPT UI, even though the raw MCP protocol result looks fine).
                # It must instead hand back to the model with an instruction to
                # call list_transactions again.
                print("\ntools/call verify_reference_id (transactions purpose — must not self-render)")
                if not await reset_backend_state(base_url):
                    c.skip("verify_reference_id transactions-purpose check", "could not reset backend state")
                else:
                    pending_query = "show my shopping transactions"
                    unverified = await session.call_tool("list_transactions", {"query": pending_query})
                    c.check("fresh unverified query asks for email",
                            "email id to continue" in text_of(unverified).lower())
                    await session.call_tool(
                        "submit_verification_email",
                        {"email": KNOWN_CUSTOMER_EMAIL, "query": pending_query},
                    )
                    ref_id = await fetch_debug_reference_id(base_url)
                    if ref_id is None:
                        c.skip("verify_reference_id transactions-purpose check", "DEBUG_EXPOSE_REFERENCE_ID not enabled")
                    else:
                        verify_result = await session.call_tool("verify_reference_id", {"reference_id": ref_id})
                        verify_meta = verify_result.meta or {}
                        c.check("verify_reference_id result has NO widget meta (text-only)",
                                not verify_meta.get("openai/outputTemplate"), str(verify_meta))
                        verify_text = text_of(verify_result).lower()
                        c.check("verify_reference_id tells the model to call list_transactions again",
                                "list_transactions" in verify_text, text_of(verify_result))
                        c.check("verify_reference_id echoes the original query back",
                                pending_query in text_of(verify_result), text_of(verify_result))

                        followup = await session.call_tool("list_transactions", {"query": pending_query})
                        followup_meta = followup.meta or {}
                        c.check("the follow-up list_transactions call DOES render the widget",
                                followup_meta.get("openai/outputTemplate") == TRANSACTIONS_WIDGET_URI)

    print(f"\n{'All checks passed.' if not c.failures else f'{c.failures} check(s) FAILED.'}\n")
    return 1 if c.failures else 0


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    sys.exit(asyncio.run(main(base)))
