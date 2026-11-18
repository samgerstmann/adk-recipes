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

"""Callbacks: request routing, workflow gates, state logging, LLM judge.

Orchestrator:
- before_agent  `route_request`        request ID / file from the message
- before_tool   `gate_tool_call`       halt check + enforced human approval
                                       + human-provided repair values
- after_tool    `after_tool_call`      records when approval was requested
- after_agent   `llm_judge_gate`       LLM-as-judge on the final response
Sub-agents:
- before_agent  `check_step_prerequisites`
- after_agent   `log_step_result`
- before_model  `inject_document` (DocumentExtractionAgent)
"""

import base64
import json
import logging
import os
import re
from typing import Any

from google import genai
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import BaseTool, ToolContext
from google.genai import types

from . import documents
from . import state_store as ss
from .models import JudgeVerdict
from .prompts import JUDGE_PROMPT

logger = logging.getLogger(__name__)

SUB_AGENT_TOOLS = set(ss.ALL_STEPS)


def _user_text(content: types.Content | None) -> str:
    if not content or not content.parts:
        return ""
    return " ".join(p.text for p in content.parts if getattr(p, "text", None)).strip()


def _text(msg: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part(text=msg)])


# --------------------------------------------------------------------------
# Human approval / repair classification (pure, unit tested)
# --------------------------------------------------------------------------

_DELEGATE = re.compile(
    r"\b(yourself|you decide|your call|your decision|on my behalf|for me|"
    r"auto[- ]?approve|automatically|without (me|a human|human|review)|"
    r"you (can |should |may )?approve|decide (it )?for)\b"
)
_DECLINE_START = re.compile(r"^(no|nope|reject|rejected|decline|declined|deny|denied|don'?t)\b")
_NEGATION = re.compile(r"\b(no|not|don'?t|do not|never|reject|decline|deny|cancel)\b")
_APPROVE = re.compile(
    r"^(yes|y|yep|yeah|approve|approved|lgtm|confirm|confirmed|ok(ay)?[, ]+approve)\b"
    r"|\bi (hereby |explicitly )?approve\b"
)


def classify_approval(text: str) -> str:
    """Classify a human reply: approve | decline | delegate | unclear."""
    t = re.sub(r"\s+", " ", (text or "").lower()).strip(" .!")
    if not t:
        return "unclear"
    if _DELEGATE.search(t):
        return "delegate"
    if _DECLINE_START.search(t):
        return "decline"
    if _NEGATION.search(t):
        return "unclear"
    if _APPROVE.search(t):
        return "approve"
    return "unclear"


def value_in_message(value: str, message: str) -> bool:
    """Was a repair value typed by the human in their message?"""

    def norm(s: str) -> str:  # ignore case, spaces, "$" and thousands separators
        return re.sub(r"[\s$,]", "", (s or "").lower())

    return bool(norm(value)) and norm(value) in norm(message)


def approval_gate(process: dict | None, invocation_id: str, user_text: str) -> str | None:
    """Return an error message if LoanDecisionAgent may NOT run (pure)."""
    if not process:
        return "Human approval required: no workflow state for this request."
    pricing = process.get("steps", {}).get(ss.STEP_PRICING, {}).get("status")
    if pricing not in ss.DONE_STATUSES:
        return "Human approval required: analysis is not complete (PricingAgent has not finished)."
    approval = process.get("approval") or {}
    if not approval.get("requested_invocation_id"):
        return "Human approval required: the summary has not been presented to a human yet."
    if approval.get("requested_invocation_id") == invocation_id:
        return (
            "Human approval required: present the summary and wait; approval must "
            "come from the human in a later message."
        )
    verdict = classify_approval(user_text)
    if verdict == "delegate":
        return (
            "Human approval required: the agent cannot approve on the user's behalf. "
            "A human loan officer must reply with an explicit 'yes' / 'I approve'."
        )
    if verdict != "approve":
        return "Human approval required: the latest message is not an explicit approval (reply 'yes' or 'no')."
    return None


# --------------------------------------------------------------------------
# Orchestrator callbacks
# --------------------------------------------------------------------------


def _inline_document(content: types.Content | None) -> dict | None:
    for i, part in enumerate((content.parts or []) if content else []):
        blob = getattr(part, "inline_data", None)
        if blob and blob.data and (blob.mime_type or "") in documents.MIME_TYPES.values():
            data = blob.data if isinstance(blob.data, bytes) else base64.b64decode(blob.data)
            return {
                "mime_type": blob.mime_type,
                "data": base64.b64encode(data).decode(),
                "filename": getattr(blob, "display_name", None) or f"inline_document_{i}",
            }
    return None


def _clear_outputs(state) -> None:
    for key in ss.AGENT_OUTPUT_KEY_MAP.values():
        if state.get(key) is not None:
            state[key] = None


async def route_request(callback_context: CallbackContext) -> types.Content | None:
    """Pick up the request ID / application file from the user's message."""
    state = callback_context.state
    content = callback_context.user_content
    text = _user_text(content)
    request_id = documents.extract_request_id(text)
    filename = documents.extract_filename(text)
    if filename and not request_id:
        request_id = documents.request_id_for(filename)
    if request_id and request_id != state.get("loan_request_id"):
        if state.get("loan_request_id"):
            logger.info("Switching request %s -> %s", state.get("loan_request_id"), request_id)
            _clear_outputs(state)
            state["application_file"] = None
            state["inline_document"] = None
        state["loan_request_id"] = request_id
    if filename:
        state["application_file"] = filename
    elif request_id and not state.get("application_file"):
        try:  # bundled samples / known files resolve from the request ID
            state["application_file"] = documents.filename_for(request_id)
        except Exception as e:  # listing a bucket can fail; extraction retries later
            logger.debug("filename_for failed: %s", e)
    inline = _inline_document(content)
    if inline:
        state["inline_document"] = inline
    # Record an explicit human "no" deterministically (audit trail).
    rid = state.get("loan_request_id")
    if rid and classify_approval(text) == "decline":
        store = ss.get_state_store()
        process = store.get(rid)
        if process and process.get("overall_status") == ss.OVERALL_AWAITING_HUMAN:
            store.record_human_decision(rid, False, callback_context.user_id, text)
    return None


async def gate_tool_call(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext) -> dict | None:
    """Halt sub-agents when paused; enforce human approval and human repairs."""
    try:
        request_id = tool_context.state.get("loan_request_id")
        if tool.name == "repair_application_field":
            if not value_in_message(str(args.get("value", "")), _user_text(tool_context.user_content)):
                return {
                    "status": "error",
                    "message": "Repair rejected: the value must be typed by the human operator in their message.",
                }
            return None
        if tool.name not in SUB_AGENT_TOOLS or not request_id:
            return None
        store = ss.get_state_store()
        process = store.get(request_id)
        if process:
            halt = ss.determine_halt_action(tool.name, process.get("overall_status"), process.get("issues", []))
            if halt:
                logger.warning("Halting %s for %s: %s", tool.name, request_id, halt["error"])
                return halt
        if tool.name == ss.STEP_LOAN_DECISION:
            error = approval_gate(process, tool_context.invocation_id, _user_text(tool_context.user_content))
            if error:
                logger.warning("Approval gate blocked LoanDecisionAgent for %s", request_id)
                return {"error": error}
            store.record_human_decision(
                request_id, True, tool_context.user_id, _user_text(tool_context.user_content)
            )
        return None
    except Exception as e:
        logger.exception("gate_tool_call failed")
        if tool.name == ss.STEP_LOAN_DECISION:  # fail closed
            return {"error": f"Human approval check failed: {e!s}"}
        return None


async def after_tool_call(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: Any
) -> dict | None:
    """Mark when the approval question is asked; surface pauses immediately."""
    request_id = tool_context.state.get("loan_request_id")
    if tool.name not in SUB_AGENT_TOOLS or not request_id:
        return None
    store = ss.get_state_store()
    process = store.get(request_id)
    if not process:
        return None
    if tool.name == ss.STEP_PRICING and process["steps"][ss.STEP_PRICING].get("status") in ss.DONE_STATUSES:
        store.request_approval(request_id, tool_context.invocation_id)
        return None
    if process.get("overall_status") == ss.OVERALL_PENDING_APPROVAL:
        issue = ss.open_issue(process) or {}
        note = (
            f"WORKFLOW PAUSED for human repair: {issue.get('description')}. "
            "Do not call further sub-agents; explain how to repair."
        )
        if isinstance(tool_response, dict):
            return {**tool_response, "workflow_status": note}
        return {"result": tool_response, "workflow_status": note}
    return None


# --------------------------------------------------------------------------
# Sub-agent callbacks (state check + state logging)
# --------------------------------------------------------------------------


def tool_already_answered(llm_request: LlmRequest) -> bool:
    """True once the request holds a function response (pure)."""
    return any(
        part.function_response
        for content in llm_request.contents or []
        for part in content.parts or []
    )


def single_tool_call(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """Each sub-agent tool runs once: afterwards the model must answer.

    With output_schema + tools, Gemini occasionally re-calls the same tool in
    a loop instead of emitting the structured answer. Once a tool result is
    in the request, function calling is switched off so the next
    model turn is the final JSON.
    """
    if tool_already_answered(llm_request):
        llm_request.config = llm_request.config or types.GenerateContentConfig()
        llm_request.config.tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="NONE")
        )
    return None


def trace_model_response(callback_context: CallbackContext, llm_response) -> None:
    """Log each sub-agent model turn when LOAN_AGENT_TRACE=1 (debugging)."""
    if os.getenv("LOAN_AGENT_TRACE") != "1":
        return None
    parts = (llm_response.content.parts if llm_response.content else None) or []
    summary = []
    for p in parts:
        if p.function_call:
            summary.append(f"CALL {p.function_call.name}")
        elif p.text:
            summary.append(("THOUGHT " if p.thought else "TEXT ") + p.text[:120].replace("\n", " "))
    logger.info("TRACE %s: %s", callback_context.agent_name, " | ".join(summary) or llm_response.error_message)
    return None


async def check_step_prerequisites(callback_context: CallbackContext) -> types.Content | None:
    """Refuse to run a step whose predecessors are not complete."""
    request_id = callback_context.state.get("loan_request_id")
    step = callback_context.agent_name
    if not request_id:
        return _text(f"Cannot run {step}: no loan_request_id (format SBL-YYYY-XXXXX).")
    store = ss.get_state_store()
    if not store.can_proceed_to_step(request_id, step):
        process = store.get(request_id) or {}
        issue = ss.open_issue(process) or {}
        return _text(
            f"Cannot proceed to {step}: previous steps are not complete "
            f"(status: {process.get('overall_status')}; {issue.get('description', '')})."
        )
    if step == ss.STEP_UNDERWRITING:
        callback_context.state["eligibility_rules"] = json.dumps(documents.eligibility_rules())
    return None


async def log_step_result(callback_context: CallbackContext) -> None:
    """Persist the step output; pause the workflow if it needs review."""
    request_id = callback_context.state.get("loan_request_id")
    step = callback_context.agent_name
    if not request_id:
        return None
    output = callback_context.state.get(ss.AGENT_OUTPUT_KEY_MAP.get(step, ""))
    if hasattr(output, "model_dump"):
        output = output.model_dump()
    try:
        ss.get_state_store().record_step_result(request_id, step, output)
    except Exception:
        logger.exception("log_step_result failed for %s", step)
    return None


async def _artifact_document(callback_context: CallbackContext) -> types.Part | None:
    try:
        keys = await callback_context.list_artifacts()
        for key in keys or []:
            part = await callback_context.load_artifact(key)
            if part is not None and getattr(part, "inline_data", None):
                return part
    except Exception as e:  # no artifact service, etc.
        logger.debug("No artifacts: %s", e)
    return None


async def inject_document(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """Attach the application document to the extraction model request.

    AgentTool does not forward multimodal content to sub-agents, so the
    document is resolved here: selected file -> uploaded inline document ->
    session artifact -> the file mapped to the request ID.
    """
    state = callback_context.state
    part = None
    name = state.get("application_file")
    if name:
        data, mime = documents.load_application(name)
        part = types.Part(inline_data=types.Blob(mime_type=mime, data=data))
    if part is None and isinstance(state.get("inline_document"), dict):
        doc = state["inline_document"]
        part = types.Part(
            inline_data=types.Blob(mime_type=doc["mime_type"], data=base64.b64decode(doc["data"]))
        )
    if part is None:
        part = await _artifact_document(callback_context)
    if part is None and state.get("loan_request_id"):
        mapped = documents.filename_for(state["loan_request_id"])
        if mapped:
            data, mime = documents.load_application(mapped)
            part = types.Part(inline_data=types.Blob(mime_type=mime, data=data))
    if part is None:
        logger.warning("No application document found for extraction")
        return None
    llm_request.contents.append(
        types.Content(
            role="user",
            parts=[types.Part(text="Here is the loan application document to extract data from:"), part],
        )
    )
    return None


# --------------------------------------------------------------------------
# LLM-as-judge (after-agent)
# --------------------------------------------------------------------------


def _trajectory(callback_context: CallbackContext) -> tuple[list[str], str, str]:
    tools: list[str] = []
    final_response, user_message = "", ""
    for event in callback_context.session.events or []:
        if event.invocation_id != callback_context.invocation_id or not event.content:
            continue
        for part in event.content.parts or []:
            if part.function_call and part.function_call.name:
                tools.append(part.function_call.name)
            if part.text and not getattr(part, "thought", False):
                if event.content.role == "user":
                    user_message = part.text
                elif event.content.role == "model":
                    final_response = part.text
    return tools, final_response, user_message or _user_text(callback_context.user_content)


async def llm_judge_gate(callback_context: CallbackContext) -> types.Content | None:
    """Validate trajectory and grounding of the orchestrator's response."""
    if (os.getenv("LLM_JUDGE") or "on").lower() in ("off", "false", "0", "no"):
        return None
    request_id = callback_context.state.get("loan_request_id")
    try:
        tools, final_response, user_message = _trajectory(callback_context)
        if not final_response:
            return None
        outputs = {}
        for key in ss.AGENT_OUTPUT_KEY_MAP.values():
            value = callback_context.state.get(key)
            if value:
                outputs[key] = value.model_dump() if hasattr(value, "model_dump") else value
        prompt = JUDGE_PROMPT.format(
            agent_outputs=json.dumps(outputs, indent=2, default=str) if outputs else "No agent outputs",
            tool_sequence=" -> ".join(tools) or "No tools called",
            user_message=user_message or "No user message",
            final_response=final_response,
        )
        client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION") or "global",
        )
        response = await client.aio.models.generate_content(
            model=os.getenv("JUDGE_MODEL") or os.getenv("MODEL_NAME"),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=JudgeVerdict
            ),
        )
        verdict = JudgeVerdict.model_validate_json(response.text or "")
        callback_context.state["_llm_judge_audit"] = {
            "loan_request_id": request_id,
            "verdict": verdict.model_dump(),
            "tool_sequence": tools,
        }
        if verdict.is_valid:
            logger.info("LLM judge APPROVED response for %s", request_id)
            return None
        logger.warning("LLM judge BLOCKED response for %s: %s", request_id, verdict.reasoning)
        return _text(
            "Quality check: the response above could not be verified against the agent "
            "outputs and should not be relied on. Please retry your request.\n\n"
            f"Reference ID: {request_id or 'N/A'}\nJudge: {verdict.reasoning}"
        )
    except Exception as e:
        logger.error("LLM judge error: %s", e, exc_info=True)
        return _text(
            "Quality check could not run for the response above; treat it as unverified "
            f"and retry.\n\nReference: {request_id or 'N/A'}"
        )
