"""
transaction_query.py

Rule-based natural language query engine for bank transaction data.
No LLM — uses regex/keyword matching + pandas.
"""

import re
from datetime import datetime
from difflib import get_close_matches
from typing import Any

import pandas as pd
from dateutil import parser as dateparser


MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


_DATE_LIKE = re.compile(
    r"\d{4}|\d{1,2}[/.-]\d{1,2}|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|today|yesterday",
    re.IGNORECASE,
)

MERCHANT_STOP_WORDS = {
    "merchant", "merchants", "transaction", "transactions", "spending",
    "spent", "total", "breakdown", "category", "categories", "top",
    "month", "monthly", "year", "annual", "show", "list", "all",
    "how", "much", "many", "what", "where", "when", "which",
    "give", "display", "filter", "between", "above", "below",
    "over", "under", "more", "less", "than", "and", "the",
    "from", "for", "with", "avg", "average", "min", "max",
    "cheapest", "most", "biggest", "largest", "highest", "lowest",
    "least", "fewest", "frequent", "often", "times", "count",
    "number", "salary", "income", "credit", "debit", "expense",
    "expenses", "cost", "pay", "paid", "payment", "payments",
    "balance", "trend", "per", "by", "each", "every", "was",
    "is", "are", "were", "did", "do", "does", "has", "have",
    "had", "been", "being", "will", "would", "could", "should",
}


class TransactionQueryEngine:
    def __init__(self, excel_path: str):
        self.df = pd.read_excel(excel_path)
        self.df["transaction_date"] = pd.to_datetime(self.df["transaction_date"])
        self.df["year_month"] = self.df["transaction_date"].dt.to_period("M").astype(str)
        self._categories = self.df["category"].unique().tolist()
        self._subcategories = self.df["subcategory"].unique().tolist()
        self._merchants = self.df["merchant"].unique().tolist()

    # ── public API ──────────────────────────────────────────────

    def query(self, q: str) -> dict[str, Any] | pd.DataFrame:
        """Parse a natural-language query and return results."""
        q_lower = q.lower().strip()
        parsed = self._parse(q_lower)
        filtered = self._apply_filters(self.df, parsed["filters"])
        result = self._execute(filtered, parsed)
        return result

    def query_with_intent(self, q: str) -> tuple[str, dict[str, Any] | pd.DataFrame, dict]:
        """Like `query`, but also returns the detected intent and parsed filters.

        Callers that need to decide *how* to present a result (e.g. a rich
        list-view widget vs. a one-line aggregate answer) need the intent —
        `query()` alone throws it away.
        """
        q_lower = q.lower().strip()
        parsed = self._parse(q_lower)
        filtered = self._apply_filters(self.df, parsed["filters"])
        result = self._execute(filtered, parsed)
        return parsed["intent"], result, parsed

    # ── parser ──────────────────────────────────────────────────

    def _parse(self, q: str) -> dict:
        return {
            "intent": self._detect_intent(q),
            "filters": self._extract_filters(q),
            "group_by": self._detect_grouping(q),
            "top_n": self._extract_top_n(q),
            "sort": self._detect_sort(q),
        }

    def _detect_intent(self, q: str) -> str:
        # order matters: more specific patterns first
        if re.search(r"\b(breakdown|split|dis[ttribution])\b", q):
            return "breakdown"
        if re.search(r"\b(per\s+|by\s+month|by\s+category|by\s+merchant|by\s+subcategory)\b", q):
            return "breakdown"
        if re.search(r"\bcheapest|lowest|least\s+expensive|minimum\s+transaction\b", q):
            return "min"
        if re.search(r"\bmost\s+expensive|largest\s+transaction|biggest\s+transaction|highest\s+single\b", q):
            return "max"
        if re.search(r"\btop\s+\d|top\s+|highest\s+total|most\s+(spent|frequent)|biggest\s+(spender|category|merchant)\b", q):
            return "top_n"
        if re.search(r"\b(how\s+many|count|number\s+of|times)\b", q):
            return "count"
        if re.search(r"\b(average|avg|mean)\b", q):
            return "avg"
        if re.search(r"\b(cheapest|lowest|least|minimum|min)\b", q):
            return "min"
        if re.search(r"\b(most\s+expensive|highest|maximum|max|biggest|largest)\b", q):
            return "max"
        if re.search(r"\b(balance)\b", q):
            return "balance"
        if re.search(r"\b(list|show|all|display|give\s+me|what\s+are)\b", q):
            return "list"
        if re.search(r"\b(spend|spent|total|how\s+much|cost|pay|paid|payment)\b", q):
            return "sum"
        if re.search(r"\b(income|credit|salary|earned|earning|received)\b", q):
            return "sum"
        # default: list
        return "list"

    def _detect_grouping(self, q: str) -> str | None:
        if re.search(r"\b(by\s+month|monthly|each\s+month|per\s+month|month\s+wise|months)\b", q):
            return "year_month"
        if re.search(r"\b(by\s+category|per\s+category|category\s+wise|category\s+breakdown|categories)\b", q):
            return "category"
        if re.search(r"\b(by\s+merchant|per\s+merchant|merchant\s+wise|merchant\s+breakdown|merchants)\b", q):
            return "merchant"
        if re.search(r"\b(by\s+subcategory|per\s+subcategory|subcategory\s+wise)\b", q):
            return "subcategory"
        if re.search(r"\b(by\s+type|by\s+transaction\s+type)\b", q):
            return "transaction_type"
        # auto-detect: if breakdown intent but no explicit group, pick one
        return None

    def _detect_sort(self, q: str) -> dict | None:
        if re.search(r"\b(most\s+expensive|biggest|largest|highest)\b", q):
            return {"by": "amount_gbp", "ascending": False}
        if re.search(r"\b(cheapest|smallest|lowest|least)\b", q):
            return {"by": "amount_gbp", "ascending": True}
        if re.search(r"\b(most\s+frequent|most\s+often|most\s+times)\b", q):
            return {"by": "count", "ascending": False}
        return None

    def _extract_top_n(self, q: str) -> int | None:
        m = re.search(r"top\s+(\d+)", q)
        if m:
            return int(m.group(1))
        # only match standalone "top"/"highest"/"biggest"/"most" — NOT when
        # combined with "expensive", "cheapest", "costly" (those are single-txn queries)
        if re.search(r"\btop\b", q):
            return 10
        if re.search(r"\b(highest|biggest)\s+(spender|category|merchant|total|amount|expense)\b", q):
            return 10
        if re.search(r"\bmost\s+(spent|frequent|often|times|frequently)\b", q):
            return 10
        return None

    # ── filter extraction ───────────────────────────────────────

    def _extract_filters(self, q: str) -> dict:
        filters: dict[str, Any] = {}

        # transaction type
        if re.search(r"\b(credit|income|salary|earned|received)\b", q) and not re.search(r"\b(debit)\b", q):
            filters["transaction_type"] = "Credit"
        elif re.search(r"\b(debit|expense|spent|payment|spending|costs?|purchase)\b", q) and not re.search(r"\b(credit)\b", q):
            filters["transaction_type"] = "Debit"

        # category
        cat = self._match_category(q)
        if cat:
            filters["category"] = cat

        # subcategory
        sub = self._match_subcategory(q)
        if sub:
            filters["subcategory"] = sub

        # merchant
        merch = self._match_merchant(q)
        if merch:
            filters["merchant"] = merch

        # date range
        date_filter = self._extract_date_filter(q)
        if date_filter:
            filters.update(date_filter)

        # amount range
        amt = self._extract_amount_filter(q)
        if amt:
            filters.update(amt)

        return filters

    def _match_category(self, q: str) -> str | None:
        # normalise "&" <-> "and" both ways — several category names use "&"
        # ("Food & Dining", "Bills & Utilities", ...) but users type "and".
        q_and = q.replace("&", "and")
        for cat in self._categories:
            cat_lower = cat.lower()
            if cat_lower in q or cat_lower.replace("&", "and") in q_and:
                return cat
        # fuzzy fallback — skip common query words
        skip = {"show", "list", "all", "how", "much", "many", "what", "total",
                "breakdown", "spending", "spent", "top", "month", "year", "balance",
                "merchant", "merchants", "transaction", "transactions", "the", "and",
                "for", "from", "per", "by", "each", "over", "under", "most", "least"}
        words = re.findall(r"\b\w+\b", q)
        for w in words:
            if w in skip or len(w) < 3:
                continue
            matches = get_close_matches(w, [c.lower() for c in self._categories], n=1, cutoff=0.75)
            if matches:
                for cat in self._categories:
                    if cat.lower() == matches[0]:
                        return cat
        return None

    def _match_subcategory(self, q: str) -> str | None:
        for sub in self._subcategories:
            if sub and sub.lower() in q:
                return sub
        return None

    def _match_merchant(self, q: str) -> str | None:
        # concept words that look like merchant names but aren't
        concept_words = {"merchant", "merchants", "income", "salary", "transfer",
                         "transfers", "refund", "other", "cash", "bank", "taxes"}
        # normalize apostrophes for matching
        q_norm = q.replace("'", "").replace("\u2019", "")
        # try exact first — use word boundaries to avoid "merchant" matching "merchants"
        for m in self._merchants:
            m_norm = m.lower().replace("'", "").replace("\u2019", "")
            pattern = r"\b" + re.escape(m_norm) + r"\b"
            if re.search(pattern, q_norm):
                if m.lower() in concept_words:
                    continue
                return m
        # fuzzy fallback — skip stop words
        words = re.findall(r"\b\w[\w\s&']*\b", q_norm)
        for w in words:
            w_clean = w.strip().lower()
            if w_clean in MERCHANT_STOP_WORDS or w_clean in concept_words or len(w_clean) < 2:
                continue
            # try with and without apostrophes
            matches = get_close_matches(w_clean, [m.lower().replace("'", "") for m in self._merchants], n=1, cutoff=0.7)
            if matches:
                for m in self._merchants:
                    if m.lower().replace("'", "") == matches[0]:
                        return m
        return None

    def _extract_date_filter(self, q: str) -> dict | None:
        # "in january 2026" / "in jan 2026"
        m = re.search(r"\b(in|during|for)\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\b", q)
        if m:
            month = MONTH_NAMES[m.group(2)]
            year = int(m.group(3))
            start = pd.Timestamp(year, month, 1)
            end = start + pd.offsets.MonthEnd(0)
            return {"date_from": start, "date_to": end}

        # "between X and Y" or "from X to Y" — only treat as a date range if
        # both sides actually look like dates, so amount ranges like
        # "between 50 and 200" aren't swallowed here (dateutil will happily,
        # and wrongly, parse a bare "50" as the year 2050).
        m = re.search(r"(?:between|from)\s+(.+?)\s+(?:and|to)\s+(.+?)(?:\s*$)", q)
        if m and _DATE_LIKE.search(m.group(1)) and _DATE_LIKE.search(m.group(2)):
            try:
                d1 = dateparser.parse(m.group(1), dayfirst=True)
                d2 = dateparser.parse(m.group(2), dayfirst=True)
                if d1 and d2:
                    ts1 = pd.Timestamp(d1)
                    ts2 = pd.Timestamp(d2)
                    # if only month+year given (no day), extend end to end of month
                    raw2 = m.group(2).strip()
                    if not re.search(r"\d{1,2}[/-]\d{1,2}|\d{1,2}\s+\w+\s+\d{4}", raw2):
                        ts2 = ts2 + pd.offsets.MonthEnd(0)
                    return {"date_from": ts1, "date_to": ts2}
            except (ValueError, TypeError):
                pass

        # "last month" / "this month"
        if "last month" in q:
            today = pd.Timestamp.now()
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - pd.Timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            return {"date_from": last_month_start, "date_to": last_month_end}

        if "this month" in q:
            today = pd.Timestamp.now()
            start = today.replace(day=1)
            return {"date_from": start, "date_to": today}

        # "last year" / "this year"
        if "last year" in q:
            year = pd.Timestamp.now().year - 1
            return {"date_from": pd.Timestamp(year, 1, 1), "date_to": pd.Timestamp(year, 12, 31)}

        if "this year" in q:
            year = pd.Timestamp.now().year
            return {"date_from": pd.Timestamp(year, 1, 1), "date_to": pd.Timestamp(year, 12, 31)}

        # "in 2026" / "in 2025"
        m = re.search(r"\b(?:in|during|for)\s+(\d{4})\b", q)
        if m:
            year = int(m.group(1))
            return {"date_from": pd.Timestamp(year, 1, 1), "date_to": pd.Timestamp(year, 12, 31)}

        # specific date: "on 2026-01-15" or "on January 5 2026"
        m = re.search(r"\b(?:on|dated?)\s+([\d\w\s,/-]+?)(?:\s*[?!.]?\s*$|\s+(?:where|and|but|filter))", q)
        if m:
            try:
                raw = m.group(1).strip().rstrip("?.!")
                d = dateparser.parse(raw, dayfirst=True)
                if d and len(raw) > 3:
                    ts = pd.Timestamp(d)
                    return {"date_from": ts, "date_to": ts}
            except (ValueError, TypeError):
                pass

        return None

    def _extract_amount_filter(self, q: str) -> dict | None:
        # "over £100" / "above 100" / "more than 50"
        m = re.search(r"(?:over|above|more\s+than|greater\s+than|exceeding|at\s+least)\s*[£]?\s*([\d,]+\.?\d*)", q)
        if m:
            val = float(m.group(1).replace(",", ""))
            return {"amount_min": val}

        # "under £50" / "below 50" / "less than 20"
        m = re.search(r"(?:under|below|less\s+than|fewer\s+than|at\s+most)\s*[£]?\s*([\d,]+\.?\d*)", q)
        if m:
            val = float(m.group(1).replace(",", ""))
            return {"amount_max": val}

        # "between 50 and 200"
        m = re.search(r"between\s*[£]?\s*([\d,]+\.?\d*)\s+and\s*[£]?\s*([\d,]+\.?\d*)", q)
        if m:
            lo = float(m.group(1).replace(",", ""))
            hi = float(m.group(2).replace(",", ""))
            return {"amount_min": lo, "amount_max": hi}

        # "exactly £123.45"
        m = re.search(r"(?:exactly|precisely)\s*[£]?\s*([\d,]+\.?\d*)", q)
        if m:
            val = float(m.group(1).replace(",", ""))
            return {"amount_exact": val}

        return None

    # ── filter application ──────────────────────────────────────

    def _apply_filters(self, df: pd.DataFrame, filters: dict) -> pd.DataFrame:
        result = df.copy()

        if "transaction_type" in filters:
            result = result[result["transaction_type"] == filters["transaction_type"]]
        if "category" in filters:
            result = result[result["category"] == filters["category"]]
        if "subcategory" in filters:
            result = result[result["subcategory"] == filters["subcategory"]]
        if "merchant" in filters:
            result = result[result["merchant"] == filters["merchant"]]
        if "date_from" in filters:
            result = result[result["transaction_date"] >= filters["date_from"]]
        if "date_to" in filters:
            result = result[result["transaction_date"] <= filters["date_to"]]
        if "amount_min" in filters:
            result = result[abs(result["amount_gbp"]) >= filters["amount_min"]]
        if "amount_max" in filters:
            result = result[abs(result["amount_gbp"]) <= filters["amount_max"]]
        if "amount_exact" in filters:
            result = result[abs(result["amount_gbp"]) == filters["amount_exact"]]

        return result

    # ── executor ────────────────────────────────────────────────

    def _execute(self, df: pd.DataFrame, parsed: dict) -> dict[str, Any] | pd.DataFrame:
        intent = parsed["intent"]
        group_by = parsed["group_by"]
        top_n = parsed["top_n"]

        if len(df) == 0:
            return {"intent": intent, "message": "No transactions match the given filters.", "count": 0}

        # top_n — only when intent is top_n (not when overridden by min/max/avg/etc)
        if intent == "top_n":
            return self._do_top_n(df, parsed)

        # breakdown intent
        if intent == "breakdown" or (group_by and intent in ("sum", "count", "avg")):
            return self._do_breakdown(df, group_by, intent)

        if intent == "sum":
            total = df["amount_gbp"].sum()
            return {
                "intent": "sum",
                "total_amount_gbp": round(total, 2),
                "transaction_count": len(df),
                "filter_summary": self._filter_summary(df),
            }

        if intent == "count":
            return {
                "intent": "count",
                "count": len(df),
                "filter_summary": self._filter_summary(df),
            }

        if intent == "avg":
            avg = df["amount_gbp"].mean()
            return {
                "intent": "avg",
                "average_amount_gbp": round(avg, 2),
                "transaction_count": len(df),
                "filter_summary": self._filter_summary(df),
            }

        if intent == "min":
            idx = df["amount_gbp"].abs().idxmin()
            row = df.loc[idx]
            return {
                "intent": "min",
                "transaction": self._row_to_dict(row),
            }

        if intent == "max":
            idx = df["amount_gbp"].abs().idxmax()
            row = df.loc[idx]
            return {
                "intent": "max",
                "transaction": self._row_to_dict(row),
            }

        if intent == "balance":
            # latest balance in filtered set
            latest = df.sort_values("transaction_date").iloc[-1]
            earliest = df.sort_values("transaction_date").iloc[0]
            return {
                "intent": "balance",
                "latest_balance_gbp": round(float(latest["balance_gbp"]), 2),
                "latest_date": str(latest["transaction_date"].date()),
                "earliest_balance_gbp": round(float(earliest["balance_gbp"]), 2),
                "earliest_date": str(earliest["transaction_date"].date()),
            }

        # default: list
        if parsed["sort"]:
            sort_cfg = parsed["sort"]
            if sort_cfg["by"] == "count" and group_by:
                return self._do_breakdown(df, group_by, "count", ascending=sort_cfg["ascending"])
            df = df.sort_values(sort_cfg["by"], ascending=sort_cfg["ascending"])

        display_cols = ["transaction_id", "transaction_date", "transaction_type",
                        "category", "subcategory", "merchant", "description",
                        "amount_gbp", "balance_gbp"]
        return df[display_cols].reset_index(drop=True)

    def _do_breakdown(self, df: pd.DataFrame, group_by: str | None, intent: str, ascending: bool = True) -> pd.DataFrame:
        if not group_by:
            # auto-pick: category if no category filter, else merchant
            if "category" not in df.columns or df["category"].nunique() <= 1:
                group_by = "merchant"
            else:
                group_by = "category"

        if intent == "count":
            result = df.groupby(group_by).size().reset_index(name="count")
            result = result.sort_values("count", ascending=False)
        elif intent == "avg":
            result = df.groupby(group_by)["amount_gbp"].agg(["mean", "count"]).reset_index()
            result.columns = [group_by, "average_amount", "transaction_count"]
            result["average_amount"] = result["average_amount"].round(2)
            result = result.sort_values("average_amount")
        else:  # sum
            result = df.groupby(group_by)["amount_gbp"].sum().reset_index()
            result.columns = [group_by, "total_amount_gbp"]
            result["total_amount_gbp"] = result["total_amount_gbp"].round(2)
            result = result.sort_values("total_amount_gbp")

        return result.reset_index(drop=True)

    def _do_top_n(self, df: pd.DataFrame, parsed: dict) -> pd.DataFrame:
        n = parsed["top_n"] or 10
        sort_cfg = parsed["sort"]

        # group by category or merchant
        group_by = parsed["group_by"]
        if not group_by:
            if "merchant" not in df.columns or df["merchant"].nunique() <= 1:
                group_by = "category"
            elif "category" not in df.columns or df["category"].nunique() <= 1:
                group_by = "merchant"
            else:
                # if querying "top merchants", group by merchant; else category
                group_by = "merchant"

        if sort_cfg and sort_cfg["by"] == "count":
            result = df.groupby(group_by).size().reset_index(name="count")
            result = result.sort_values("count", ascending=False).head(n)
        else:
            result = df.groupby(group_by)["amount_gbp"].agg(
                total_spent="sum",
                transaction_count="count",
                average="mean"
            ).reset_index()
            result["total_spent"] = result["total_spent"].round(2)
            result["average"] = result["average"].round(2)
            ascending = sort_cfg["ascending"] if sort_cfg else False
            # sort by absolute value so largest spending (most negative) comes first
            result["_abs_spent"] = result["total_spent"].abs()
            result = result.sort_values("_abs_spent", ascending=ascending).head(n)
            result = result.drop(columns=["_abs_spent"])

        return result.reset_index(drop=True)

    # ── helpers ─────────────────────────────────────────────────

    def _row_to_dict(self, row: pd.Series) -> dict:
        d = row.to_dict()
        d["transaction_date"] = str(d["transaction_date"].date())
        return d

    def _filter_summary(self, df: pd.DataFrame) -> str:
        parts = []
        if len(df) < len(self.df):
            parts.append(f"{len(df)} of {len(self.df)} transactions")
        return ", ".join(parts) if parts else "all transactions"
