"""Pydantic request/response models for the Lloyds Banking Demo Plugin API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    id: str
    merchant: str
    category: str
    amount: float
    date: str
    icon: str
    status: str


class TransactionSummaryResponse(BaseModel):
    total_spending: float
    transaction_count: int
    latest_transaction: Transaction | None
    spending_by_category: dict[str, float]
    average_transaction: float
    transactions: list[Transaction]
    lloyds_web_app_url: str
    ui_card: str = Field(description="Pre-rendered markdown card for chat display")


class MortgageSalaryRequest(BaseModel):
    annual_salary: float = Field(gt=0, description="Applicant's gross annual salary in GBP")


class MortgageOfferResponse(BaseModel):
    annual_salary: float
    loan_amount: float
    interest_rate: float
    term_years: int
    estimated_monthly_payment: float
    total_repayable: float
    total_interest: float
    application_status: str
    application_id: str
    eligibility: dict
    next_steps: list[str]
    generated_at: str
    lloyds_web_app_url: str
    ui_card: str = Field(description="Pre-rendered markdown card for chat display")


class MortgagePromptResponse(BaseModel):
    message: str
    awaiting: str = "annual_salary"
