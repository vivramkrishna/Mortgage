"""Real transaction data + dynamic natural-language querying for the MCP
`list_transactions` tool.

Wraps `transaction_query.TransactionQueryEngine` (rule-based regex/keyword
parsing over pandas — no LLM call) and turns its output into exactly two
shapes the tool needs:

  * a "list" result  -> a widget payload (rendered as the transactions card)
  * anything else     -> a short plain-text answer (sum/count/avg/min/max/
                          balance/breakdown/top_n — no widget)

Real customer data lives in `data/customer_001_transactions_1_year_gbp (1).xlsx`
(603 transactions, Aug 2025 - Jul 2026, 18 categories, 68 merchants).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from transaction_query import TransactionQueryEngine  # noqa: E402

DATA_PATH = _REPO_ROOT / "data" / "customer_001_transactions_1_year_gbp (1).xlsx"

# One icon per category — kept small and monochrome-friendly rather than the
# mixed emoji set the old mock data used, so the card reads as one system.
CATEGORY_ICONS: dict[str, str] = {
    "Housing": "\U0001F3E0",  # house
    "Income": "\U0001F4B0",  # money bag
    "Healthcare": "⚕️",  # medical
    "Entertainment": "\U0001F3AC",  # clapper
    "Bills & Utilities": "\U0001F4A1",  # bulb
    "Cash": "\U0001F3E7",  # ATM
    "Insurance": "\U0001F6E1️",  # shield
    "Food & Dining": "\U0001F37D️",  # plate
    "Shopping": "\U0001F6CD️",  # bags
    "Transport": "\U0001F697",  # car
    "Travel": "✈️",  # plane
    "Education": "\U0001F393",  # cap
    "Fees & Charges": "\U0001F9FE",  # receipt
    "Investment": "\U0001F4C8",  # chart
    "Loans & Credit": "\U0001F3E6",  # bank
    "Other": "●",  # dot
    "Taxes & Government": "\U0001F3DB️",  # classical building
    "Transfers": "\U0001F501",  # repeat
}
DEFAULT_ICON = "\U0001F4B3"  # card

# Cap the widget to a manageable number of rows even for unfiltered/broad
# queries ("show me all my transactions") — the underlying dataset spans a
# full year (600+ rows), which would make the card unusable.
MAX_LIST_ROWS = 30

_engine: TransactionQueryEngine | None = None


def get_engine() -> TransactionQueryEngine:
    global _engine
    if _engine is None:
        _engine = TransactionQueryEngine(str(DATA_PATH))
    return _engine


def gbp(n: float) -> str:
    n = float(n)
    sign = "-" if n < 0 else ""
    return f"{sign}£{abs(n):,.2f}"


def _iso_date(value: Any) -> str:
    try:
        return pd.Timestamp(value).date().isoformat()
    except (ValueError, TypeError):
        return str(value)


def _row_to_txn(row: pd.Series) -> dict[str, Any]:
    category = row.get("category")
    return {
        "id": row.get("transaction_id"),
        "merchant": row.get("merchant"),
        "category": category,
        "subcategory": row.get("subcategory"),
        "description": row.get("description"),
        "amount": round(float(row["amount_gbp"]), 2),
        "type": row.get("transaction_type"),
        "date": _iso_date(row.get("transaction_date")),
        "icon": CATEGORY_ICONS.get(category, DEFAULT_ICON),
    }


def _describe_filters(parsed: dict) -> str | None:
    f = parsed.get("filters") or {}
    if not f:
        return None
    parts: list[str] = []
    if "transaction_type" in f:
        parts.append(f["transaction_type"].lower())
    if "category" in f:
        parts.append(f["category"])
    if "subcategory" in f:
        parts.append(f["subcategory"])
    if "merchant" in f:
        parts.append(f["merchant"])
    if "date_from" in f or "date_to" in f:
        d1, d2 = f.get("date_from"), f.get("date_to")
        if d1 is not None and d2 is not None and pd.Timestamp(d1).date() == pd.Timestamp(d2).date():
            parts.append(str(pd.Timestamp(d1).date()))
        else:
            lo = pd.Timestamp(d1).date() if d1 is not None else "…"
            hi = pd.Timestamp(d2).date() if d2 is not None else "…"
            parts.append(f"{lo} to {hi}")
    if "amount_exact" in f:
        parts.append(f"exactly {gbp(f['amount_exact'])}")
    elif "amount_min" in f or "amount_max" in f:
        lo, hi = f.get("amount_min"), f.get("amount_max")
        if lo is not None and hi is not None:
            parts.append(f"{gbp(lo)}–{gbp(hi)}")
        elif lo is not None:
            parts.append(f"over {gbp(lo)}")
        elif hi is not None:
            parts.append(f"under {gbp(hi)}")
    return ", ".join(parts) if parts else None


def build_list_payload(df: pd.DataFrame, filter_note: str | None) -> dict[str, Any]:
    """Transaction-list result -> the widget's structuredContent shape."""
    df_sorted = df.sort_values("transaction_date", ascending=False)
    total_count = len(df_sorted)
    shown = df_sorted.head(MAX_LIST_ROWS)
    txns = [_row_to_txn(row) for _, row in shown.iterrows()]

    debits = df_sorted[df_sorted["transaction_type"] == "Debit"]
    total_spending = round(float(debits["amount_gbp"].abs().sum()), 2) if len(debits) else 0.0
    average_transaction = round(float(debits["amount_gbp"].abs().mean()), 2) if len(debits) else 0.0

    by_cat: dict[str, float] = {}
    if len(debits):
        grouped = debits.groupby("category")["amount_gbp"].apply(lambda s: round(float(s.abs().sum()), 2))
        by_cat = dict(grouped.sort_values(ascending=False).items())

    return {
        "transactions": txns,
        "shown_count": len(txns),
        "total_count": total_count,
        "total_spending": total_spending,
        "transaction_count": total_count,
        "average_transaction": average_transaction,
        "spending_by_category": by_cat,
        "latest_transaction": txns[0] if txns else None,
        "filter_note": filter_note,
    }


def _format_table(df: pd.DataFrame, intro: str, limit: int = 10) -> str:
    if df.empty:
        return "No results match that."
    cols = list(df.columns)
    group_col = cols[0]
    lines = []
    for _, row in df.head(limit).iterrows():
        label = str(row[group_col])
        parts = []
        for c in cols[1:]:
            v = row[c]
            if isinstance(v, float):
                parts.append(gbp(v) if ("amount" in c or "spent" in c) else f"{v:g}")
            else:
                parts.append(str(v))
        lines.append(f"- {label}: {', '.join(parts)}")
    more = len(df) - min(limit, len(df))
    tail = f"\n(+{more} more)" if more > 0 else ""
    return f"{intro}:\n" + "\n".join(lines) + tail


def format_answer(intent: str, result: Any, parsed: dict) -> str:
    if isinstance(result, dict) and result.get("count") == 0:
        return result.get("message", "No transactions match that.")

    if intent == "sum":
        n = result["transaction_count"]
        return (
            f"Total: {gbp(result['total_amount_gbp'])} across {n} "
            f"transaction{'s' if n != 1 else ''} ({result['filter_summary']})."
        )
    if intent == "count":
        n = result["count"]
        return f"{n} transaction{'s' if n != 1 else ''} match ({result['filter_summary']})."
    if intent == "avg":
        return (
            f"Average: {gbp(result['average_amount_gbp'])} across "
            f"{result['transaction_count']} transactions ({result['filter_summary']})."
        )
    if intent == "min":
        t = result["transaction"]
        return f"Smallest: {gbp(t['amount_gbp'])} at {t['merchant']} on {t['transaction_date']} ({t['category']})."
    if intent == "max":
        t = result["transaction"]
        return f"Largest: {gbp(t['amount_gbp'])} at {t['merchant']} on {t['transaction_date']} ({t['category']})."
    if intent == "balance":
        return (
            f"Balance was {gbp(result['latest_balance_gbp'])} as of {result['latest_date']} "
            f"(was {gbp(result['earliest_balance_gbp'])} on {result['earliest_date']})."
        )
    if intent in ("breakdown", "top_n") and isinstance(result, pd.DataFrame):
        intro = "Top results" if intent == "top_n" else "Breakdown"
        return _format_table(result, intro)

    return "No transactions match that."


def run_query(query: str) -> dict[str, Any]:
    """Answer a natural-language transaction question with no LLM call.

    Returns {"mode": "widget"|"text", "text": str, "structured": dict|None}.
    "widget" is returned only for plain listing queries; every aggregation
    (sum/count/avg/min/max/balance/breakdown/top_n) is "text".
    """
    engine = get_engine()
    intent, result, parsed = engine.query_with_intent(query or "")

    if intent == "list" and isinstance(result, dict):
        # zero matches — `_execute` short-circuits to a dict even for "list"
        return {"mode": "text", "text": "No transactions match that.", "structured": None}

    if intent == "list":
        filter_note = _describe_filters(parsed)
        payload = build_list_payload(result, filter_note)
        if not payload["transactions"]:
            text = "No transactions match that."
        else:
            latest = payload["latest_transaction"]
            scope = f" ({filter_note})" if filter_note else ""
            text = (
                f"Showing {payload['shown_count']} of {payload['total_count']} "
                f"transaction{'s' if payload['total_count'] != 1 else ''}{scope}. "
                f"Most recent: {latest['merchant']} {gbp(latest['amount'])} on {latest['date']}."
            )
        return {"mode": "widget", "text": text, "structured": payload}

    text = format_answer(intent, result, parsed)
    return {"mode": "text", "text": text, "structured": None}
