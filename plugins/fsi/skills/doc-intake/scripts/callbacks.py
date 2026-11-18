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

"""Callbacks: request routing, human-in-the-loop gates, optional LLM judge.

- before_agent `route_request`   picks up a package file name from the message
- before_tool  `gate_tool_call`  corrections must use values the human typed;
                                 accepts need an explicit human confirmation;
                                 approval is enforced (see approval_gate)
- after_agent  `llm_judge_gate`  optional grounding check (LLM_JUDGE=on)
"""

import json
import logging
import os
import re
from typing import Any

from google import genai
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import BaseTool, ToolContext
from google.genai import types
from pydantic import BaseModel, Field

from . import intake
from .prompts import JUDGE_PROMPT
from .state_store import STATUS_APPROVED, get_state_store

logger = logging.getLogger(__name__)


def _user_text(content: types.Content | None) -> str:
    if not content or not content.parts:
        return ""
    return " ".join(p.text for p in content.parts if getattr(p, "text", None)).strip()


# --------------------------------------------------------------------------
# Human message classification (pure, unit tested)
# --------------------------------------------------------------------------

_DELEGATE = re.compile(
    r"\b(yourself|you decide|your call|your decision|on my behalf|for me|"
    r"auto[- ]?approve|automatically|without (me|a human|human|review)|"
    r"you (can |should |may )?(approve|accept)|decide (it )?for)\b"
)
_DECLINE_START = re.compile(r"^(no|nope|reject|rejected|decline|declined|deny|denied|don'?t)\b")
_NEGATION = re.compile(r"\b(no|not|don'?t|do not|never|reject|decline|deny|cancel)\b")
_APPROVE = re.compile(
    r"^(yes|y|yep|yeah|approve|approved|lgtm|confirm|confirmed|ok(ay)?[, ]+approve)\b"
    r"|\bi (hereby |explicitly )?approve\b"
)
_ACCEPT = re.compile(r"\b(accept|accepted|confirm|confirmed|keep|correct as (is|extracted)|looks (good|right|correct))\b")
_NOTE = re.compile(r"\b(?:reviewer note|note|comment|reason)\s*[:\-]\s*(.+)$", re.I | re.S)


def split_note(text: str) -> tuple[str, str]:
    """('approve ...' head, note) - the note is whatever follows 'Note:'."""
    match = _NOTE.search(text or "")
    if not match:
        return (text or "").strip(), ""
    return text[: match.start()].strip(), match.group(1).strip()


def classify_approval(text: str) -> str:
    """approve | decline | delegate | unclear (the reviewer note is ignored)."""
    head, _ = split_note(text)
    t = re.sub(r"\s+", " ", head.lower()).strip(" .!")
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


def is_accept_message(text: str) -> bool:
    t = (text or "").lower()
    return bool(_ACCEPT.search(t)) and not _DELEGATE.search(t)


def value_in_message(value: str, message: str) -> bool:
    """Was a correction value typed by the human in their message?"""

    def norm(s: str) -> str:  # ignore case, spaces, "$" and thousands separators
        return re.sub(r"[\s$,]", "", (s or "").lower())

    return bool(norm(value)) and norm(value) in norm(message)


def approval_gate(doc: dict | None, invocation_id: str, user_text: str) -> tuple[str | None, str]:
    """(error, reviewer_note). error is None when the human approval is valid."""
    if not doc:
        return "Human approval required: no package has been processed in this session.", ""
    if doc.get("status") == STATUS_APPROVED:
        return "Record already approved.", ""
    if doc.get("split_problems"):
        return "Cannot approve: the package split has problems; reprocess it first.", ""
    pending = intake.open_items(doc)
    if pending:
        names = ", ".join(f"{i['doc_type']}.{i['field']}" for i in pending[:8])
        return f"Cannot approve: {len(pending)} field(s) still need review ({names}).", ""
    if doc.get("last_change_invocation") == invocation_id:
        return (
            "Human approval required: present the final record and wait; approval must come "
            "from the human in a later message.", "",
        )
    verdict = classify_approval(user_text)
    if verdict == "delegate":
        return (
            "Human approval required: the agent cannot approve on the user's behalf. A human "
            "reviewer must reply with an explicit 'Approve' / 'I approve'.", "",
        )
    if verdict != "approve":
        return "Human approval required: the latest message is not an explicit approval.", ""
    _, note = split_note(user_text)
    if intake.failed_checks(doc) and not note:
        return (
            "Reviewer note required: checks failed, so approve with a note, e.g. "
            "'Approve. Note: discrepancies referred to the applicant'.", "",
        )
    return None, note


# --------------------------------------------------------------------------
# Orchestrator callbacks
# --------------------------------------------------------------------------


async def route_request(callback_context: CallbackContext) -> types.Content | None:
    """Remember a package file name mentioned in the message."""
    match = re.search(r"[\w.-]+\.(?:pdf|png|jpe?g)", _user_text(callback_context.user_content), re.I)
    if match:
        callback_context.state["mentioned_file"] = match.group(0)
    return None


async def gate_tool_call(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext) -> dict | None:
    """Human-in-the-loop gates (fail closed for approval)."""
    text = _user_text(tool_context.user_content)
    try:
        if tool.name == "correct_field":
            if not value_in_message(str(args.get("value", "")), text):
                return {"status": "error", "message": "Correction rejected: the value must be typed by the human reviewer in their message."}
        elif tool.name == "accept_fields":
            if not is_accept_message(text):
                return {"status": "error", "message": "Accept rejected: the human reviewer must explicitly confirm (e.g. 'accept the remaining fields')."}
        elif tool.name == "approve_record":
            store = get_state_store()
            package_id = tool_context.state.get("package_id")
            doc = store.get(package_id) if package_id else None
            error, note = approval_gate(doc, tool_context.invocation_id, text)
            if error:
                logger.warning("Approval gate blocked %s: %s", package_id, error)
                return {"status": "error", "message": error}
            args["reviewer_note"] = note  # the note comes from the human, not the model
            store.save(package_id, intake.approve(doc, tool_context.user_id, note, text))
        return None
    except Exception as e:
        logger.exception("gate_tool_call failed")
        if tool.name == "approve_record":
            return {"status": "error", "message": f"Human approval check failed: {e!s}"}
        return None


# --------------------------------------------------------------------------
# Optional LLM-as-judge (after-agent)
# --------------------------------------------------------------------------


class JudgeVerdict(BaseModel):
    is_valid: bool = Field(description="Whether the response may be shown")
    grounded_in_context: bool = Field(description="All values traceable to the package state")
    reasoning: str = Field(description="Brief explanation")


async def llm_judge_gate(callback_context: CallbackContext) -> types.Content | None:
    """Grounding check of the final response against the saved package state."""
    if (os.getenv("LLM_JUDGE") or "off").lower() not in ("on", "true", "1", "yes"):
        return None
    final = ""
    for event in callback_context.session.events or []:
        if event.invocation_id == callback_context.invocation_id and event.content and event.content.role == "model":
            for part in event.content.parts or []:
                if part.text and not part.thought:
                    final = part.text
    if not final:
        return None
    package_id = callback_context.state.get("package_id")
    doc = get_state_store().get(package_id) if package_id else None
    context = json.dumps(intake.summary(doc), default=str)[:20000] if doc else "No package state"
    try:
        client = genai.Client(vertexai=True, project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                              location=os.getenv("GOOGLE_CLOUD_LOCATION") or "global")
        response = await client.aio.models.generate_content(
            model=os.getenv("JUDGE_MODEL") or os.getenv("MODEL_NAME") or "gemini-3.8-flash",
            contents=JUDGE_PROMPT.format(context=context, user_message=_user_text(callback_context.user_content), final_response=final),
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json", response_schema=JudgeVerdict),
        )
        verdict = JudgeVerdict.model_validate_json(response.text or "")
        callback_context.state["_llm_judge_audit"] = verdict.model_dump()
        if verdict.is_valid:
            return None
        return types.Content(role="model", parts=[types.Part(text=(
            "Quality check: the response above could not be verified against the package state; "
            f"treat it as unverified. Judge: {verdict.reasoning}"))])
    except Exception as e:
        logger.error("LLM judge error: %s", e)
        return None


