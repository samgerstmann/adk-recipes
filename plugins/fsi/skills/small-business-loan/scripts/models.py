# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Structured outputs of the sub-agents and the LLM judge."""

from typing import Literal

from pydantic import BaseModel, Field


class BusinessAddress(BaseModel):
    street: str = Field(default="", description="Street address of the business")
    city: str = Field(default="", description="City")
    state: str = Field(default="", description="State code (e.g., IL, CA)")
    zip_code: str = Field(default="", description="ZIP code")


class LoanApplicationData(BaseModel):
    """Data extracted from a small business loan application."""

    business_name: str = Field(default="", description="Legal name of the business applying for the loan")
    business_type: str = Field(default="", description="Legal structure (e.g., 'LLC', 'S-Corp', 'Sole Proprietorship')")
    ein: str = Field(default="", description="Employer Identification Number (EIN) of the business")
    industry: str = Field(default="", description="Industry or business sector (e.g., 'Food & Beverage')")
    years_in_business: str = Field(default="", description="Number of years the business has been operating")
    number_of_employees: str = Field(default="", description="Number of employees in the business")
    business_address: BusinessAddress | None = Field(default=None, description="Physical address of the business")
    owner_name: str = Field(default="", description="Full name of the business owner or primary applicant")
    owner_email: str = Field(default="", description="Email address of the business owner")
    owner_phone: str = Field(default="", description="Phone number of the business owner")
    annual_revenue: str = Field(default="", description="Annual revenue of the business (e.g., '$850,000')")
    net_profit: str = Field(default="", description="Annual net profit of the business (e.g., '$120,000')")
    existing_debt: str = Field(default="", description="Total existing business debt (e.g., '$50,000' or 'None')")
    loan_amount_requested: str = Field(default="", description="Amount of loan requested (e.g., '$150,000'); empty if the field is blank")
    # Plain str: Vertex AI rejects an empty string inside a response-schema enum.
    loan_purpose: str = Field(
        default="",
        description="Purpose of the loan: Equipment, Expansion, Working Capital, Real Estate, Refinance or Other; empty if blank",
    )
    loan_term_months: str = Field(default="", description="Requested loan term in months (e.g., '60')")
    collateral_offered: str = Field(default="", description="Description of collateral offered to secure the loan")


class FieldComparison(BaseModel):
    field_name: str = Field(description="Name of the field being compared")
    application_value: str = Field(description="Value from the loan application document")
    internal_value: str = Field(description="Value from the bank's internal records")
    match_status: Literal["Match", "No Match", "Partial Match"] = Field(description="Whether the values match")
    notes: str = Field(default="", description="Additional notes about the comparison")


class UnderwritingReport(BaseModel):
    """Validation against internal records plus eligibility."""

    validation_status: Literal["MATCH", "NO MATCH"] = Field(
        description="Whether application data matches internal records"
    )
    matched_fields: list[FieldComparison] = Field(default_factory=list, description="Fields that match")
    discrepancies: list[FieldComparison] = Field(default_factory=list, description="Fields that don't match")
    eligibility_status: Literal["ELIGIBLE", "INELIGIBLE", "REVIEW"] = Field(
        description="ELIGIBLE, INELIGIBLE, or REVIEW (needs manual review)"
    )
    matched_rule: str | None = Field(default=None, description="The eligibility rule that determined the status")
    risk_flags: list[str] = Field(default_factory=list, description="Risk flags identified during underwriting")
    summary: str = Field(description="Executive summary of the underwriting assessment")
    recommendation: str = Field(description="Recommendation on whether to proceed with the loan")


class PricingResult(BaseModel):
    status: Literal["success", "error"] = Field(description="Status of the pricing calculation")
    interest_rate: str = Field(description="Annual interest rate (e.g., '7.25%')")
    monthly_payment: str = Field(description="Estimated monthly payment (e.g., '$2,985.00')")
    total_interest: str = Field(description="Total interest over the loan term")
    risk_tier: str = Field(description="Risk tier used for pricing (e.g., 'Tier 2 - Moderate Risk')")
    rate_justification: str = Field(description="Brief explanation of how the rate was determined")


class LoanDecisionResult(BaseModel):
    status: Literal["success", "error"] = Field(description="Status of the decision operation")
    decision: Literal["APPROVED", "DENIED", "CONDITIONAL"] = Field(description="Final loan decision")
    decision_letter_id: str = Field(description="Decision letter reference ID (e.g., 'DL-2025-00142-001')")
    approved_amount: str = Field(default="", description="Approved loan amount")
    approved_rate: str = Field(default="", description="Approved interest rate")
    approved_term: str = Field(default="", description="Approved loan term")
    conditions: list[str] = Field(default_factory=list, description="Conditions attached to the decision")
    message: str = Field(description="Summary message about the decision")


class JudgeVerdict(BaseModel):
    is_valid: bool = Field(description="Whether the response should be shown to the user")
    trajectory_correct: bool = Field(description="Whether the tool sequence follows expected patterns")
    grounded_in_context: bool = Field(description="Whether all values are traceable to agent outputs")
    response_complete: bool = Field(description="Whether the response includes all required information")
    reasoning: str = Field(description="Brief explanation of the verdict")
