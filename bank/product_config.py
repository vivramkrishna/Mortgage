"""Product configuration for indicative affordability quotes.

Kept separate from `underwriting.py`'s policy constants: underwriting prices a
real application against a specific applicant's credit file, while this
config drives a rough "what could I borrow" estimate before any application
exists. Centralising it here means the multiplier/rate can change (or later
be swapped for a live rates feed) without touching the calculation code.
"""
from __future__ import annotations

# Standard affordability multiplier applied to combined annual income.
AFFORDABILITY_INCOME_MULTIPLE = 4.5

# Stubbed indicative rate — a real integration would fetch this from a rates
# service; callers should treat it as "current 5yr fixed" until that lands.
INDICATIVE_RATE_5YR_FIXED = 4.49

# Mortgage term assumed when the customer hasn't specified one.
DEFAULT_TERM_YEARS = 30
