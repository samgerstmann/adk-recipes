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

"""Human approval gate, repair gate, request routing, documents and setup (offline)."""

from types import SimpleNamespace

import pytest
from google.genai import types

from scripts import callbacks, documents, tools
from scripts import state_store as ss
from scripts.setup import SpecError, build_env

RID = "SBL-2025-00142"


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setenv("STATE_BACKEND", "memory")
    monkeypatch.setattr(ss, "_STORE", {})
    return ss.get_state_store()


def ctx(text="", invocation="inv-2", state=None):
    """Fake ToolContext / CallbackContext exposing the public API we use."""
    return SimpleNamespace(
        state=state if state is not None else {"loan_request_id": RID},
        user_content=types.Content(role="user", parts=[types.Part(text=text)]),
        invocation_id=invocation,
        user_id="officer",
        session=SimpleNamespace(id="s1"),
    )


def tool(name):
    return SimpleNamespace(name=name)


def analysed(store, rid=RID, invocation="inv-1"):
    store.create(rid)
    store.record_step_result(rid, ss.STEP_DOCUMENT_EXTRACTION, {
        "business_name": "Coffee Roasters LLC", "owner_name": "Jane Doe",
        "annual_revenue": "$850,000", "loan_amount_requested": "$150,000", "loan_term_months": "60"})
    store.record_step_result(rid, ss.STEP_UNDERWRITING, {"eligibility_status": "ELIGIBLE", "risk_flags": []})
    store.record_step_result(rid, ss.STEP_PRICING, {"status": "success", "interest_rate": "6.5%"})
    store.request_approval(rid, invocation)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("yes", "approve"), ("Yes, I approve SBL-2025-00142", "approve"), ("approve", "approve"),
        ("I hereby approve this loan", "approve"), ("approve it yourself", "delegate"),
        ("just auto-approve it", "delegate"), ("you decide", "delegate"),
        ("Can you approve it for me?", "delegate"), ("no", "decline"), ("reject it", "decline"),
        ("yes but do not finalize", "unclear"), ("what is the rate?", "unclear"), ("", "unclear"),
    ],
)
def test_classify_approval(text, expected):
    assert callbacks.classify_approval(text) == expected


def test_value_in_message():
    msg = "Repair SBL-2025-00391: loan_amount_requested = $150,000"
    assert callbacks.value_in_message("$150,000", msg)
    assert callbacks.value_in_message("150000", msg)
    assert not callbacks.value_in_message("$250,000", msg)
    assert not callbacks.value_in_message("", msg)


async def test_loan_decision_blocked_in_same_turn_as_summary(fresh_store):
    analysed(fresh_store, invocation="inv-1")
    result = await callbacks.gate_tool_call(tool("LoanDecisionAgent"), {}, ctx("yes", invocation="inv-1"))
    assert "Human approval required" in result["error"]


async def test_loan_decision_blocked_when_delegated(fresh_store):
    analysed(fresh_store)
    result = await callbacks.gate_tool_call(tool("LoanDecisionAgent"), {}, ctx("approve it yourself"))
    assert "cannot approve on the user's behalf" in result["error"]
    assert fresh_store.get(RID)["approval"]["decision"] is None


async def test_loan_decision_blocked_before_analysis(fresh_store):
    fresh_store.create(RID)
    result = await callbacks.gate_tool_call(tool("LoanDecisionAgent"), {}, ctx("yes"))
    assert "analysis is not complete" in result["error"]


async def test_loan_decision_allowed_after_explicit_yes(fresh_store):
    analysed(fresh_store)
    assert await callbacks.gate_tool_call(tool("LoanDecisionAgent"), {}, ctx("Yes")) is None
    approval = fresh_store.get(RID)["approval"]
    assert approval["decision"] == "approved" and approval["decided_by"] == "officer"


def test_finalize_requires_recorded_approval(fresh_store):
    analysed(fresh_store)
    state = {
        "loan_request_id": RID,
        "DocumentExtractionAgent_output": {"loan_amount_requested": "$150,000", "loan_term_months": "60"},
        "UnderwritingAgent_output": {"eligibility_status": "ELIGIBLE"},
        "PricingAgent_output": {"interest_rate": "6.5%"},
    }
    assert "Human approval required" in tools.finalize_loan_decision(ctx(state=state))["message"]
    fresh_store.record_human_decision(RID, True, "officer", "yes")
    assert tools.finalize_loan_decision(ctx(state=state))["decision"] == "APPROVED"


async def test_halt_blocks_sub_agents_when_paused(fresh_store):
    fresh_store.create(RID)
    fresh_store.record_step_result(RID, ss.STEP_DOCUMENT_EXTRACTION, {"business_name": "x"})
    result = await callbacks.gate_tool_call(tool("UnderwritingAgent"), {}, ctx("continue"))
    assert "Pending approval" in result["error"]


async def test_repair_requires_value_typed_by_human(fresh_store):
    args = {"field_name": "loan_amount_requested", "value": "$150,000"}
    blocked = await callbacks.gate_tool_call(tool("repair_application_field"), args, ctx("please fix it"))
    assert "Repair rejected" in blocked["message"]
    ok = await callbacks.gate_tool_call(
        tool("repair_application_field"), args, ctx("loan_amount_requested = $150,000"))
    assert ok is None


def test_repair_tool_resumes_workflow(fresh_store):
    rid = "SBL-2025-00391"
    fresh_store.create(rid)
    fresh_store.record_step_result(rid, ss.STEP_DOCUMENT_EXTRACTION, {
        "business_name": "Coffee Roasters LLC", "owner_name": "Jane Doe", "annual_revenue": "$850,000"})
    state = {"loan_request_id": rid}
    assert tools.repair_application_field("bogus", "1", ctx(state=state))["status"] == "error"
    result = tools.repair_application_field("loan_amount_requested", "$150,000", ctx(state=state))
    assert result["status"] == "success"
    assert state["DocumentExtractionAgent_output"]["loan_amount_requested"] == "$150,000"
    assert tools.repair_application_field("owner_name", "X", ctx(state=state))["status"] == "error"


def test_check_process_status_initializes_and_resumes(fresh_store):
    state = {"loan_request_id": RID}
    assert tools.check_process_status(ctx(state=state))["action"] == "proceed_to_analysis"
    analysed(fresh_store, rid="SBL-2025-00777")
    state = {"loan_request_id": "SBL-2025-00777"}
    result = tools.check_process_status(ctx(state=state))
    assert result["action"] == "awaiting_human_approval"
    assert state["PricingAgent_output"]["interest_rate"] == "6.5%"


async def test_route_request_picks_file_and_id(fresh_store):
    state = {}
    await callbacks.route_request(ctx("Process sample_application_incomplete.pdf", state=state))
    assert state["loan_request_id"] == "SBL-2025-00391"
    assert state["application_file"] == "sample_application_incomplete.pdf"
    state["DocumentExtractionAgent_output"] = {"x": 1}
    await callbacks.route_request(ctx("Now process SBL-2025-00142", state=state))
    assert state["loan_request_id"] == RID and state["DocumentExtractionAgent_output"] is None


async def test_route_request_records_human_decline(fresh_store):
    analysed(fresh_store)
    await callbacks.route_request(ctx("no", state={"loan_request_id": RID}))
    assert fresh_store.get(RID)["overall_status"] == ss.OVERALL_DECLINED


def test_single_tool_call_disables_tools_after_first_result():
    from google.adk.models.llm_request import LlmRequest

    request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="price it")])])
    callbacks.single_tool_call(None, request)
    assert request.config.tool_config is None
    request.contents.append(types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(name="calculate_loan_pricing", response={"status": "success"}))]))
    callbacks.single_tool_call(None, request)
    assert request.config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE


def test_bundled_documents():
    files = documents.list_application_files()
    assert files == ["sample_application_complete.pdf", "sample_application_incomplete.pdf"]
    data, mime = documents.load_application("../applications/sample_application_complete.pdf")
    assert mime == "application/pdf" and data.startswith(b"%PDF")
    assert documents.filename_for("SBL-2025-00142") == "sample_application_complete.pdf"
    assert documents.request_id_for("my_app.pdf").startswith("SBL-2026-")
    assert len(documents.eligibility_rules()["rules"]) == 5


def test_list_and_select_application():
    listing = tools.list_applications()
    assert {"filename": "sample_application_complete.pdf", "request_id": RID} in listing["applications"]
    state = {}
    assert tools.select_application("nope.pdf", ctx(state=state))["status"] == "error"
    assert tools.select_application("sample_application_complete.pdf", ctx(state=state))["loan_request_id"] == RID


def test_setup_build_env_quick_start():
    env, report = build_env({"gcp_project_id": "demo-project"})
    assert env["STATE_BACKEND"] == "memory" and env["APPLICATIONS_SOURCE"] == ""
    assert env["GOOGLE_CLOUD_LOCATION"] == "global"
    assert any("2 bundled samples" in line for line in report)


def test_setup_build_env_validation(tmp_path):
    with pytest.raises(SpecError):
        build_env({"gcp_project_id": ""})
    with pytest.raises(SpecError):
        build_env({"gcp_project_id": "p", "state_backend": "redis"})
    with pytest.raises(SpecError):
        build_env({"gcp_project_id": "p", "applications_source": str(tmp_path)})
    rules = tmp_path / "rules.json"
    rules.write_text('{"rules": [{"id": "r1", "action": "REVIEW"}]}')
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    env, _ = build_env({"gcp_project_id": "p", "gcp_region": "europe-west4", "state_backend": "local",
                        "applications_source": str(tmp_path), "eligibility_rules": str(rules)})
    assert env["ELIGIBILITY_RULES_PATH"] == str(rules) and env["GOOGLE_CLOUD_LOCATION"] == "europe-west4"
