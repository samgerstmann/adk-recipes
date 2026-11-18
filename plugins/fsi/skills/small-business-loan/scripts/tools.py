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

"""Tools of the orchestrator and the sub-agents.

Internal records, pricing and decision letters are deterministic mocks of
the bank's systems (as in the original sample).
"""

import json
import logging
import re

from google.adk.tools.tool_context import ToolContext

from . import documents
from . import state_store as ss
from .models import LoanApplicationData

logger = logging.getLogger(__name__)

DEFAULT_LOAN_TERM_MONTHS = 60
REPAIRABLE_FIELDS = tuple(f for f in LoanApplicationData.model_fields if f != "business_address")

# Mock internal business records (simulates the bank's internal database).
MOCK_INTERNAL_RECORDS = {
    "SBL-2025-00142": {
        "business_name": "Coffee Roasters LLC",
        "business_type": "LLC",
        "ein": "00-1234567",
        "owner_name": "Jane Doe",
        "owner_email": "jane.doe@example.com",
        "owner_phone": "(555) 010-0100",
        "business_address": {
            "street": "742 Evergreen Terrace",
            "city": "Springfield",
            "state": "IL",
            "zip_code": "62704",
        },
        "industry": "Food & Beverage",
        "years_in_business": "6",
        "annual_revenue": "$850,000",
        "net_profit": "$120,000",
        "number_of_employees": "12",
        "existing_debt": "None",
        "existing_loans": [],
        "credit_score": 720,
        "account_standing": "Good",
    },
}


# --------------------------------------------------------------------------
# Orchestrator tools
# --------------------------------------------------------------------------


def list_applications() -> dict:
    """List the loan application files available to process.

    Returns:
        dict: file names, their demo request IDs and the source folder/bucket.
    """
    try:
        files = documents.list_application_files()
    except Exception as e:  # e.g. GCS permissions
        return {"status": "error", "message": f"Cannot list applications: {e!s}"}
    return {
        "status": "success",
        "source": documents.source(),
        "applications": [
            {"filename": f, "request_id": documents.request_id_for(f)} for f in files
        ],
    }


def select_application(filename: str, tool_context: ToolContext) -> dict:
    """Select the application document to process for the current request.

    Args:
        filename: File name from list_applications, e.g. sample_application_complete.pdf.

    Returns:
        dict: the selected file and request ID.
    """
    name = filename.strip().rsplit("/", 1)[-1]
    try:
        available = documents.list_application_files()
    except Exception as e:
        return {"status": "error", "message": f"Cannot list applications: {e!s}"}
    if name not in available:
        return {"status": "error", "message": f"{name} not found. Available: {available}"}
    request_id = tool_context.state.get("loan_request_id") or documents.request_id_for(name)
    tool_context.state["loan_request_id"] = request_id
    tool_context.state["application_file"] = name
    return {"status": "success", "application_file": name, "loan_request_id": request_id}


def check_process_status(tool_context: ToolContext) -> dict:
    """Check the workflow state of the current request; initialize it if new.

    This is the FIRST tool to call for every request with a request ID.

    Returns:
        dict: `action` tells the orchestrator what to do next.
    """
    request_id = tool_context.state.get("loan_request_id")
    if not request_id:
        return {
            "status": "error",
            "message": "No loan_request_id. Provide one (SBL-YYYY-XXXXX) or name an application file.",
        }
    try:
        store = ss.get_state_store()
        process = store.get(request_id)
        if process:
            result = ss.determine_process_action(process)
            _, completed = ss.find_resume_point(process)
            for step, output in completed.items():
                tool_context.state[ss.AGENT_OUTPUT_KEY_MAP[step]] = output
            # Show the data captured so far so repairs can be explained.
            extraction = process["steps"][ss.STEP_DOCUMENT_EXTRACTION].get("data")
            if extraction and result["action"] == "pending_approval":
                tool_context.state[ss.AGENT_OUTPUT_KEY_MAP[ss.STEP_DOCUMENT_EXTRACTION]] = extraction
            result["state_backend"] = store.backend
            return result
        store.create(request_id, tool_context.session.id if tool_context.session else "")
        return {
            "status": "initialized",
            "action": "proceed_to_analysis",
            "state_backend": store.backend,
            "message": f"New process initialized for {request_id}. Ready to begin processing.",
        }
    except Exception as e:
        logger.exception("check_process_status failed")
        return {"status": "error", "message": f"Error: {e!s}"}


def repair_application_field(field_name: str, value: str, tool_context: ToolContext) -> dict:
    """Apply a correction typed by a HUMAN operator to a paused application.

    Only allowed while the process is paused for repair. The value must be
    the exact value the human typed in their latest message (enforced).

    Args:
        field_name: Application field to repair, e.g. loan_amount_requested.
        value: The value provided by the human, e.g. $150,000.

    Returns:
        dict: repair result and whether the workflow can resume.
    """
    request_id = tool_context.state.get("loan_request_id")
    if not request_id:
        return {"status": "error", "message": "No loan_request_id in this session."}
    if field_name not in REPAIRABLE_FIELDS:
        return {"status": "error", "message": f"Unknown field {field_name}. Use one of {list(REPAIRABLE_FIELDS)}"}
    store = ss.get_state_store()
    process = store.get(request_id)
    if not process or process.get("overall_status") != ss.OVERALL_PENDING_APPROVAL:
        return {"status": "error", "message": f"{request_id} is not paused for repair."}
    doc = store.apply_repair(request_id, field_name, value.strip(), tool_context.user_id)
    data = doc["steps"][ss.STEP_DOCUMENT_EXTRACTION].get("data", {})
    tool_context.state[ss.AGENT_OUTPUT_KEY_MAP[ss.STEP_DOCUMENT_EXTRACTION]] = data
    still_missing = ss.missing_critical_fields(data)
    if still_missing:
        return {
            "status": "partial",
            "message": f"Repaired {field_name}; still missing: {', '.join(still_missing)}",
            "missing_fields": still_missing,
        }
    return {
        "status": "success",
        "message": (
            f"Repaired {field_name} = {value.strip()}. DocumentExtractionAgent step completed; "
            "workflow can resume from UnderwritingAgent."
        ),
        "next_step_to_execute": ss.STEP_UNDERWRITING,
    }


def reset_demo_application(tool_context: ToolContext) -> dict:
    """Delete the workflow state of the current request so a demo can rerun.

    Only available with the memory or local state backends.

    Returns:
        dict: reset result.
    """
    request_id = tool_context.state.get("loan_request_id")
    if not request_id:
        return {"status": "error", "message": "No loan_request_id in this session."}
    store = ss.get_state_store()
    if store.backend == "firestore":
        return {"status": "error", "message": "Reset is disabled for the Firestore backend."}
    store.delete(request_id)
    for key in ss.AGENT_OUTPUT_KEY_MAP.values():
        tool_context.state[key] = None
    return {"status": "success", "message": f"Workflow state of {request_id} deleted."}


# --------------------------------------------------------------------------
# Underwriting
# --------------------------------------------------------------------------


def get_internal_business_data(tool_context: ToolContext) -> dict:
    """Retrieve the bank's internal business records for underwriting.

    Returns:
        dict: internal data for the current loan request (mock).
    """
    request_id = tool_context.state.get("loan_request_id")
    if not request_id:
        return {"status": "error", "message": "loan_request_id not found in session state"}
    # Demo: unknown IDs fall back to the sample business record.
    internal = MOCK_INTERNAL_RECORDS.get(request_id) or next(iter(MOCK_INTERNAL_RECORDS.values()))
    return {"status": "success", "loan_request_id": request_id, "internal_data": json.dumps(internal)}


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------


def _parse_dollar_amount(value: str | None) -> float:
    """Parse '$150,000' into 150000.0 (0.0 if unparseable)."""
    if not value:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _determine_risk_tier(underwriting_output: dict) -> tuple[str, float]:
    """Risk tier and annual rate from the underwriting result."""
    eligibility = underwriting_output.get("eligibility_status", "REVIEW")
    risk_flags = underwriting_output.get("risk_flags", [])
    if eligibility == "ELIGIBLE" and not risk_flags:
        return "Tier 1 - Low Risk", 6.50
    if eligibility == "ELIGIBLE":
        return "Tier 2 - Moderate Risk", 7.75
    if eligibility == "REVIEW":
        return "Tier 3 - Elevated Risk", 9.25
    return "Tier 4 - High Risk", 11.00


def compute_pricing(application: dict, underwriting: dict) -> dict:
    """Amortized pricing (pure)."""
    loan_amount = _parse_dollar_amount(application.get("loan_amount_requested"))
    term_str = application.get("loan_term_months") or ""
    digits = re.sub(r"[^\d]", "", str(term_str))
    term = int(digits) if digits else DEFAULT_LOAN_TERM_MONTHS
    if loan_amount <= 0:
        return {"status": "error", "message": "Invalid loan amount"}
    if term <= 0:
        term = DEFAULT_LOAN_TERM_MONTHS
    tier, rate = _determine_risk_tier(underwriting if isinstance(underwriting, dict) else {})
    monthly_rate = rate / 100 / 12
    if monthly_rate > 0:
        payment = loan_amount * (monthly_rate * (1 + monthly_rate) ** term) / ((1 + monthly_rate) ** term - 1)
    else:
        payment = loan_amount / term
    total_interest = payment * term - loan_amount
    return {
        "status": "success",
        "interest_rate": f"{rate}%",
        "monthly_payment": f"${payment:,.2f}",
        "total_interest": f"${total_interest:,.2f}",
        "risk_tier": tier,
        "rate_justification": f"Rate of {rate}% based on {tier}. Loan amount ${loan_amount:,.0f} over {term} months.",
    }


def calculate_loan_pricing(tool_context: ToolContext) -> dict:
    """Calculate loan pricing from the application and underwriting results.

    Returns:
        dict: interest rate, monthly payment, total interest and risk tier.
    """
    application = tool_context.state.get("DocumentExtractionAgent_output")
    underwriting = tool_context.state.get("UnderwritingAgent_output")
    if not application:
        return {"status": "error", "message": "Application data not found. DocumentExtractionAgent must complete first."}
    if not underwriting:
        return {"status": "error", "message": "Underwriting data not found. UnderwritingAgent must complete first."}
    return compute_pricing(application, underwriting)


# --------------------------------------------------------------------------
# Loan decision
# --------------------------------------------------------------------------

_ELIGIBILITY_TO_DECISION = {"ELIGIBLE": "APPROVED", "INELIGIBLE": "DENIED", "REVIEW": "CONDITIONAL"}


def _resolve_decision(underwriting: object) -> tuple[str, str]:
    """(decision, eligibility) - REVIEW/unknown is referred, never auto-approved."""
    eligibility = "UNKNOWN"
    if isinstance(underwriting, dict):
        eligibility = str(underwriting.get("eligibility_status") or "UNKNOWN").upper()
    return _ELIGIBILITY_TO_DECISION.get(eligibility, "CONDITIONAL"), eligibility


def build_decision(request_id: str, application: dict, underwriting: dict, pricing: dict) -> dict:
    """Decision letter payload (pure)."""
    decision, eligibility = _resolve_decision(underwriting)
    letter_id = f"DL-{request_id.replace('SBL-', '')}-001"
    rate = pricing.get("interest_rate", "N/A") if isinstance(pricing, dict) else "N/A"
    amount = application.get("loan_amount_requested", "N/A") if isinstance(application, dict) else "N/A"
    term = application.get("loan_term_months") if isinstance(application, dict) else None
    term_str = f"{term} months" if term else "N/A"
    if decision == "APPROVED":
        conditions = [
            "Business insurance verification required within 30 days",
            "Collateral documentation to be submitted before disbursement",
        ]
        message = (
            f"Loan {request_id} has been approved. Decision letter {letter_id} has been generated. "
            f"Approved for {amount} at {rate} for {term_str}."
        )
    elif decision == "DENIED":
        conditions = []
        message = (
            f"Loan {request_id} has been denied (underwriting eligibility: {eligibility}). "
            f"Decision letter {letter_id} has been generated."
        )
    else:
        conditions = ["Manual credit review required before disbursement"]
        message = (
            f"Loan {request_id} has been referred for manual review (underwriting eligibility: "
            f"{eligibility}). Decision letter {letter_id} has been generated."
        )
    approved = decision == "APPROVED"
    return {
        "status": "success",
        "decision": decision,
        "decision_letter_id": letter_id,
        "approved_amount": amount if approved else "N/A",
        "approved_rate": rate if approved else "N/A",
        "approved_term": term_str if approved else "N/A",
        "conditions": conditions,
        "message": message,
    }


def finalize_loan_decision(tool_context: ToolContext) -> dict:
    """Finalize the loan decision and generate a decision letter reference.

    Returns:
        dict: final decision details.
    """
    state = tool_context.state
    request_id = state.get("loan_request_id")
    for key, label in (
        ("loan_request_id", "loan_request_id"),
        ("DocumentExtractionAgent_output", "Application data"),
        ("UnderwritingAgent_output", "Underwriting data"),
        ("PricingAgent_output", "Pricing data"),
    ):
        if not state.get(key):
            return {"status": "error", "message": f"{label} not found in session state"}
    # Defense in depth: the store must hold a human approval record.
    process = ss.get_state_store().get(request_id) or {}
    if (process.get("approval") or {}).get("decision") != "approved":
        return {"status": "error", "message": "Human approval required: no approval on record."}
    return build_decision(
        request_id,
        state.get("DocumentExtractionAgent_output"),
        state.get("UnderwritingAgent_output"),
        state.get("PricingAgent_output"),
    )
