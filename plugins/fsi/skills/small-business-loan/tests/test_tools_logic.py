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

"""Unit tests for pure business logic (ported from the contrib sample)."""

import pytest

from scripts.state_store import determine_halt_action
from scripts.tools import (
    _determine_risk_tier,
    _parse_dollar_amount,
    _resolve_decision,
    build_decision,
    compute_pricing,
)

APP = {"loan_amount_requested": "$150,000", "loan_term_months": "60"}


@pytest.mark.parametrize(
    "value,expected",
    [("$150,000", 150000.0), ("$2,940.97", 2940.97), ("850000", 850000.0), ("", 0.0), (None, 0.0), ("N/A", 0.0)],
)
def test_parse_dollar_amount(value, expected):
    assert _parse_dollar_amount(value) == expected


@pytest.mark.parametrize(
    "uw,tier,rate",
    [
        ({"eligibility_status": "ELIGIBLE", "risk_flags": []}, "Tier 1 - Low Risk", 6.50),
        ({"eligibility_status": "ELIGIBLE", "risk_flags": ["High debt ratio"]}, "Tier 2 - Moderate Risk", 7.75),
        ({"eligibility_status": "REVIEW", "risk_flags": []}, "Tier 3 - Elevated Risk", 9.25),
        ({"eligibility_status": "INELIGIBLE", "risk_flags": []}, "Tier 4 - High Risk", 11.00),
        ({}, "Tier 3 - Elevated Risk", 9.25),  # missing status -> REVIEW band
    ],
)
def test_determine_risk_tier(uw, tier, rate):
    assert _determine_risk_tier(uw) == (tier, rate)


class TestDetermineHaltAction:
    def test_active_allows(self):
        assert determine_halt_action("UnderwritingAgent", "active", []) is None

    def test_none_allows(self):
        assert determine_halt_action("UnderwritingAgent", None, []) is None

    def test_awaiting_human_allows(self):
        # The approval gate, not the halt check, guards LoanDecisionAgent.
        assert determine_halt_action("LoanDecisionAgent", "awaiting_human_approval", []) is None

    def test_pending_approval_uses_first_unresolved_issue(self):
        issues = [
            {"description": "Resolved issue", "resolved": True},
            {"description": "Missing field: loan_amount", "resolved": False},
        ]
        result = determine_halt_action("PricingAgent", "pending_approval", issues)
        assert "Cannot proceed" in result["error"] and "Missing field: loan_amount" in result["error"]

    @pytest.mark.parametrize("status", ["failed", "completed", "declined"])
    def test_terminal_statuses_halt(self, status):
        assert status[:6] in determine_halt_action("PricingAgent", status, [])["error"].lower()


def test_compute_pricing_tier1_sample():
    result = compute_pricing(APP, {"eligibility_status": "ELIGIBLE", "risk_flags": []})
    assert result["status"] == "success"
    assert result["interest_rate"] == "6.5%"
    assert result["monthly_payment"] == "$2,934.92"
    assert result["total_interest"] == "$26,095.33"


def test_compute_pricing_invalid_amount_and_default_term():
    assert compute_pricing({"loan_amount_requested": ""}, {})["status"] == "error"
    result = compute_pricing({"loan_amount_requested": "$60,000"}, {"eligibility_status": "REVIEW"})
    assert "60 months" in result["rate_justification"]


@pytest.mark.parametrize(
    "status,decision",
    [("ELIGIBLE", "APPROVED"), ("INELIGIBLE", "DENIED"), ("REVIEW", "CONDITIONAL"), (None, "CONDITIONAL")],
)
def test_resolve_decision(status, decision):
    assert _resolve_decision({"eligibility_status": status})[0] == decision


def test_build_decision_approved_and_referred():
    pricing = {"interest_rate": "6.5%"}
    approved = build_decision("SBL-2025-00142", APP, {"eligibility_status": "ELIGIBLE"}, pricing)
    assert approved["decision_letter_id"] == "DL-2025-00142-001"
    assert approved["approved_term"] == "60 months" and approved["approved_rate"] == "6.5%"
    referred = build_decision("SBL-2025-00142", {}, {"eligibility_status": "REVIEW"}, pricing)
    assert referred["approved_amount"] == "N/A" and referred["conditions"]
