#!/usr/bin/env python3
"""Cross-check the dynamic transaction query engine against independently
computed expected values (no LLM, no shared code with transaction_query.py —
every expectation here is recomputed straight from the workbook with plain
pandas) so a change to the parser can't silently agree with itself.

Usage:
    python scripts/verify_transaction_queries.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.transactions_data import DATA_PATH, run_query  # noqa: E402

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

df = pd.read_excel(DATA_PATH)
df["transaction_date"] = pd.to_datetime(df["transaction_date"])

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  [{PASS if ok else FAIL}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


def approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


print("\n1. Aggregation queries answer as TEXT (no widget)\n")

# groceries total
groceries = df[df["category"] == "Groceries"] if "Groceries" in df["category"].unique() else df[df["category"] == "Food & Dining"]
r = run_query("how much did I spend on food and dining")
expected_total = -float(df[(df["category"] == "Food & Dining") & (df["transaction_type"] == "Debit")]["amount_gbp"].sum())
check("mode is text", r["mode"] == "text", r["mode"])
check(
    "food & dining total matches independently computed sum",
    f"£{expected_total:,.2f}" in r["text"],
    f"expected £{expected_total:,.2f} in: {r['text']}",
)

r = run_query("how many transactions do I have")
check("count mode is text", r["mode"] == "text")
check("count matches len(df)", str(len(df)) in r["text"], r["text"])

r = run_query("what is my average transaction")
expected_avg = float(df["amount_gbp"].mean())
check("avg mode is text", r["mode"] == "text")
check("average matches df.mean()", f"£{abs(expected_avg):,.2f}" in r["text"], r["text"])

r = run_query("what was my cheapest transaction")
idx = df["amount_gbp"].abs().idxmin()
row = df.loc[idx]
check("min mode is text", r["mode"] == "text")
check("cheapest transaction matches idxmin()", row["merchant"] in r["text"], r["text"])

r = run_query("what was my most expensive transaction")
idx = df["amount_gbp"].abs().idxmax()
row = df.loc[idx]
check("max mode is text", r["mode"] == "text")
check("most expensive transaction matches idxmax()", row["merchant"] in r["text"], r["text"])

r = run_query("spending by category")
top_cat = df[df["transaction_type"] == "Debit"].groupby("category")["amount_gbp"].sum().abs().idxmax()
check("breakdown mode is text", r["mode"] == "text")
check("breakdown's top category matches groupby().sum()", top_cat in r["text"], f"expected {top_cat} in: {r['text']}")

r = run_query("top 3 merchants")
top_merchant = df.groupby("merchant")["amount_gbp"].sum().abs().sort_values(ascending=False).index[0]
check("top_n mode is text", r["mode"] == "text")
check("top merchant matches groupby().sum()", top_merchant in r["text"], f"expected {top_merchant} in: {r['text']}")

print("\n2. Listing queries render the WIDGET (no aggregation math)\n")

r = run_query("")
check("empty query mode is widget", r["mode"] == "widget")
check(
    "unfiltered total_count == len(df)",
    r["structured"]["total_count"] == len(df),
    f"{r['structured']['total_count']} vs {len(df)}",
)
check("shown_count capped at 30", r["structured"]["shown_count"] <= 30, str(r["structured"]["shown_count"]))

r = run_query("show me my shopping transactions")
expected_n = len(df[df["category"] == "Shopping"])
check("category-filtered listing mode is widget", r["mode"] == "widget")
check(
    "shopping filter count matches df filter",
    r["structured"]["total_count"] == expected_n,
    f"{r['structured']['total_count']} vs {expected_n}",
)

r = run_query("transactions with Netflix")
expected_n = len(df[df["merchant"] == "Netflix"])
check("merchant-filtered listing mode is widget", r["mode"] == "widget")
check(
    "Netflix filter count matches df filter",
    r["structured"]["total_count"] == expected_n,
    f"{r['structured']['total_count']} vs {expected_n}",
)

r = run_query("show me transactions in august 2025")
mask = (df["transaction_date"] >= "2025-08-01") & (df["transaction_date"] <= "2025-08-31")
expected_n = int(mask.sum())
check("date-filtered listing mode is widget", r["mode"] == "widget")
check(
    "August 2025 filter count matches df filter",
    r["structured"]["total_count"] == expected_n,
    f"{r['structured']['total_count']} vs {expected_n}",
)

r = run_query("show me transactions over £100")
mask = df["amount_gbp"].abs() >= 100
expected_n = int(mask.sum())
check("amount-filtered listing mode is widget", r["mode"] == "widget")
check(
    "over £100 filter count matches df filter",
    r["structured"]["total_count"] == expected_n,
    f"{r['structured']['total_count']} vs {expected_n}",
)

# regression guard for the "between 50 and 200" date/amount ambiguity bug
r = run_query("show me transactions between 50 and 200")
mask = (df["amount_gbp"].abs() >= 50) & (df["amount_gbp"].abs() <= 200)
expected_n = int(mask.sum())
check("'between 50 and 200' is treated as an amount range, not a date range", r["mode"] == "widget")
check(
    "amount-range regression: count matches df filter",
    r["structured"].get("total_count") == expected_n,
    f"{r['structured'].get('total_count')} vs {expected_n}",
)

print("\n3. Widget-vs-text totals for the same filter must agree\n")

r_widget = run_query("show me my shopping transactions")
r_text = run_query("how much did I spend on shopping")
expected = float(df[(df["category"] == "Shopping") & (df["transaction_type"] == "Debit")]["amount_gbp"].abs().sum())
check("widget's total_spending for Shopping matches text sum's total", approx(r_widget["structured"]["total_spending"], expected))
check(f"£{expected:,.2f}" + " appears in the text-mode answer", f"£{expected:,.2f}" in r_text["text"], r_text["text"])

print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) FAILED.'}\n")
sys.exit(1 if failures else 0)
