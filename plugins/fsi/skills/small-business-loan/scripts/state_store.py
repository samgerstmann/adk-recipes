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

"""Workflow state for repair & resume, with pluggable backends.

One process document per `loan_request_id` tracks the status and output of
each step, open issues, and the human approval record. The workflow logic
works on plain dicts; backends only load and save documents:

- `memory`    -- process-wide dict (Quick start, resets on restart)
- `local`     -- the same dict persisted to a JSON file
- `firestore` -- one Firestore document per request (original design)

Selected with STATE_BACKEND (default `memory`).
"""

import copy
import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STEP_DOCUMENT_EXTRACTION = "DocumentExtractionAgent"
STEP_UNDERWRITING = "UnderwritingAgent"
STEP_PRICING = "PricingAgent"
STEP_LOAN_DECISION = "LoanDecisionAgent"
ALL_STEPS = (
    STEP_DOCUMENT_EXTRACTION,
    STEP_UNDERWRITING,
    STEP_PRICING,
    STEP_LOAN_DECISION,
)

# Maps each sub-agent to the session-state key holding its output.
AGENT_OUTPUT_KEY_MAP = {step: f"{step}_output" for step in ALL_STEPS}

# Step statuses
STATUS_NOT_STARTED = "not_started"
STATUS_COMPLETED = "completed"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_AWAITING_HUMAN = "awaiting_human_approval"
STATUS_APPROVED = "approved"
DONE_STATUSES = (STATUS_COMPLETED, STATUS_APPROVED)

# Overall statuses
OVERALL_ACTIVE = "active"
OVERALL_PENDING_APPROVAL = "pending_approval"  # needs repair / review
OVERALL_AWAITING_HUMAN = "awaiting_human_approval"  # summary shown
OVERALL_COMPLETED = "completed"
OVERALL_DECLINED = "declined"
OVERALL_FAILED = "failed"

CRITICAL_FIELDS = (
    "business_name",
    "owner_name",
    "loan_amount_requested",
    "annual_revenue",
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def new_process(request_id: str, session_id: str = "") -> dict:
    return {
        "loan_request_id": request_id,
        "session_id": session_id,
        "overall_status": OVERALL_ACTIVE,
        "current_step": STEP_DOCUMENT_EXTRACTION,
        "steps": {s: {"status": STATUS_NOT_STARTED} for s in ALL_STEPS},
        "issues": [],
        "approval": None,
        "created_at": now(),
        "updated_at": now(),
    }


def missing_critical_fields(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return list(CRITICAL_FIELDS)
    return [
        f
        for f in CRITICAL_FIELDS
        if not data.get(f) or (isinstance(data.get(f), str) and not data[f].strip())
    ]


def find_resume_point(process: dict) -> tuple[str | None, dict]:
    """Return (next step to run, {completed step: output})."""
    completed: dict = {}
    next_step = None
    for step in ALL_STEPS:
        info = process.get("steps", {}).get(step, {})
        if info.get("status") in DONE_STATUSES:
            if info.get("data"):
                completed[step] = info["data"]
        elif next_step is None:
            next_step = step
    return next_step, completed


def open_issue(process: dict) -> dict | None:
    for issue in process.get("issues", []):
        if not issue.get("resolved"):
            return issue
    return None


def determine_process_action(process: dict) -> dict:
    """Decide what the orchestrator should do with an existing process."""
    status = process.get("overall_status")
    request_id = process.get("loan_request_id", "")
    next_step, completed = find_resume_point(process)
    issue = open_issue(process)
    if status == OVERALL_COMPLETED:
        action, message = "completed", f"Process {request_id} is already completed."
    elif status == OVERALL_DECLINED:
        action, message = "declined", f"Process {request_id} was declined by a human reviewer."
    elif status == OVERALL_PENDING_APPROVAL:
        action = "pending_approval"
        message = f"Process {request_id} is paused for human repair/review"
        if issue:
            message += f": {issue.get('description')}"
    elif status == OVERALL_AWAITING_HUMAN or next_step == STEP_LOAN_DECISION:
        action = "awaiting_human_approval"
        message = (
            f"Process {request_id} is analysed and waiting for a human "
            "approval decision (yes/no)."
        )
    elif next_step:
        action, message = "resume", f"Process {request_id} can resume from {next_step}."
    else:
        action, message = "return_status", f"Process {request_id} status: {status}"
    result = {
        "status": "found",
        "action": action,
        "overall_status": status,
        "current_step": process.get("current_step"),
        "next_step_to_execute": next_step,
        "completed_steps": list(completed),
        "message": message,
    }
    if issue:
        result["open_issue"] = {
            "step": issue.get("step"),
            "description": issue.get("description"),
            "missing_fields": issue.get("missing_fields", []),
        }
    return result


def determine_halt_action(tool_name: str, overall_status: str | None, issues: list) -> dict | None:
    """Return an error dict if a sub-agent call must be halted (pure)."""
    if overall_status == OVERALL_PENDING_APPROVAL:
        desc = "Unknown issue"
        for issue in issues or []:
            if not issue.get("resolved", False):
                desc = issue.get("description", desc)
                break
        return {"error": f"Cannot proceed to {tool_name}: Pending approval - {desc}"}
    if overall_status == OVERALL_FAILED:
        return {"error": f"Cannot proceed to {tool_name}: Process has failed"}
    if overall_status == OVERALL_COMPLETED:
        return {"error": f"Cannot proceed to {tool_name}: Process already completed"}
    if overall_status == OVERALL_DECLINED:
        return {"error": f"Cannot proceed to {tool_name}: Process was declined by a human reviewer"}
    return None


def check_for_issues(step: str, output: Any) -> tuple[bool, str, list[str]]:
    """Does a step's output require human review? (pure)."""
    if step == STEP_DOCUMENT_EXTRACTION:
        missing = missing_critical_fields(output)
        if missing:
            return True, f"Missing {len(missing)} critical field(s): {', '.join(missing)}", missing
    elif step == STEP_UNDERWRITING and isinstance(output, dict):
        if output.get("eligibility_status") == "INELIGIBLE":
            return True, "Application failed eligibility check - requires manual review", []
    return False, "", []


class StateStore:
    """Workflow logic over load/save of process documents."""

    backend = "base"

    # --- persistence (overridden) ---
    def _load(self, request_id: str) -> dict | None:
        raise NotImplementedError

    def _save(self, request_id: str, doc: dict) -> None:
        raise NotImplementedError

    def delete(self, request_id: str) -> None:
        raise NotImplementedError

    # --- workflow ---
    def get(self, request_id: str) -> dict | None:
        doc = self._load(request_id)
        return _json_safe(copy.deepcopy(doc)) if doc else None

    def _update(self, request_id: str, mutate) -> dict:
        doc = self._load(request_id)
        if doc is None:
            raise KeyError(f"No process for {request_id}")
        doc = copy.deepcopy(doc)
        mutate(doc)
        doc["updated_at"] = now()
        self._save(request_id, doc)
        return doc

    def create(self, request_id: str, session_id: str = "") -> dict:
        doc = new_process(request_id, session_id)
        self._save(request_id, doc)
        logger.info("Created process state for %s (%s)", request_id, self.backend)
        return doc

    def can_proceed_to_step(self, request_id: str, step: str) -> bool:
        doc = self._load(request_id)
        if not doc or step not in ALL_STEPS:
            return False
        for prev in ALL_STEPS[: ALL_STEPS.index(step)]:
            if doc["steps"].get(prev, {}).get("status") not in DONE_STATUSES:
                return False
        return True

    def record_step_result(self, request_id: str, step: str, output: Any) -> dict:
        """Persist a step's output; pause for review if it has issues."""
        needs_review, description, missing = check_for_issues(step, output)
        data = output if isinstance(output, dict) else None

        def mutate(doc: dict) -> None:
            info = doc["steps"].setdefault(step, {})
            if data is not None:
                info["data"] = data
            if needs_review:
                info["status"] = STATUS_PENDING_APPROVAL
                doc["overall_status"] = OVERALL_PENDING_APPROVAL
                doc["current_step"] = step
                doc.setdefault("issues", []).append(
                    {
                        "step": step,
                        "issue_type": "requires_review",
                        "description": description,
                        "missing_fields": missing,
                        "raised_at": now(),
                        "resolved": False,
                        "resolved_at": None,
                        "resolved_by": None,
                    }
                )
                return
            info["status"] = STATUS_COMPLETED
            info["completed_at"] = now()
            idx = ALL_STEPS.index(step)
            if step == STEP_LOAN_DECISION:
                doc["overall_status"] = OVERALL_COMPLETED
                doc["current_step"] = None
                doc["completed_at"] = now()
            elif step == STEP_PRICING:
                # Human-in-the-loop gate: the decision step waits for a human.
                doc["steps"][STEP_LOAN_DECISION]["status"] = STATUS_AWAITING_HUMAN
                doc["overall_status"] = OVERALL_AWAITING_HUMAN
                doc["current_step"] = STEP_LOAN_DECISION
            else:
                doc["current_step"] = ALL_STEPS[idx + 1]

        return self._update(request_id, mutate)

    def request_approval(self, request_id: str, invocation_id: str) -> dict:
        """Remember in which turn the summary + approval question was shown."""

        def mutate(doc: dict) -> None:
            doc["approval"] = {
                "requested_at": now(),
                "requested_invocation_id": invocation_id,
                "decision": None,
            }

        return self._update(request_id, mutate)

    def record_human_decision(
        self, request_id: str, approved: bool, user_id: str, message: str
    ) -> dict:
        def mutate(doc: dict) -> None:
            approval = doc.get("approval") or {}
            approval.update(
                {
                    "decision": "approved" if approved else "declined",
                    "decided_by": user_id,
                    "decided_at": now(),
                    "human_message": message[:500],
                }
            )
            doc["approval"] = approval
            if approved:
                doc["steps"][STEP_LOAN_DECISION]["status"] = STATUS_APPROVED
                doc["overall_status"] = OVERALL_ACTIVE
            else:
                doc["overall_status"] = OVERALL_DECLINED

        return self._update(request_id, mutate)

    def apply_repair(self, request_id: str, field: str, value: str, user_id: str) -> dict:
        """Operator repair of a DocumentExtraction field; resumes when complete."""

        def mutate(doc: dict) -> None:
            info = doc["steps"][STEP_DOCUMENT_EXTRACTION]
            data = dict(info.get("data") or {})
            data[field] = value
            info["data"] = data
            info.setdefault("repairs", []).append(
                {"field": field, "value": value, "by": user_id, "at": now()}
            )
            still_missing = missing_critical_fields(data)
            for issue in doc.get("issues", []):
                if issue.get("step") == STEP_DOCUMENT_EXTRACTION and not issue.get("resolved"):
                    issue["missing_fields"] = still_missing
                    if not still_missing:
                        issue.update(resolved=True, resolved_at=now(), resolved_by=user_id)
            if not still_missing:
                info["status"] = STATUS_COMPLETED
                info["completed_at"] = now()
                doc["overall_status"] = OVERALL_ACTIVE
                doc["current_step"] = STEP_UNDERWRITING

        return self._update(request_id, mutate)


class MemoryStateStore(StateStore):
    """Process-wide dict; optionally persisted to a JSON file (`local`)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.backend = "local" if path else "memory"
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._docs: dict[str, dict] = {}
        if self.path and self.path.exists():
            self._docs = json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def _load(self, request_id: str) -> dict | None:
        with self._lock:
            if self.path and self.path.exists():
                self._docs = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            return copy.deepcopy(self._docs.get(request_id))

    def _save(self, request_id: str, doc: dict) -> None:
        with self._lock:
            self._docs[request_id] = copy.deepcopy(_json_safe(doc))
            if self.path:
                self.path.write_text(json.dumps(self._docs, indent=2), encoding="utf-8")

    def delete(self, request_id: str) -> None:
        with self._lock:
            self._docs.pop(request_id, None)
            if self.path:
                self.path.write_text(json.dumps(self._docs, indent=2), encoding="utf-8")


class FirestoreStateStore(StateStore):
    """One document per request in `process_states` (original design)."""

    backend = "firestore"
    collection = "process_states"

    def __init__(self, database: str | None = None, client: Any = None) -> None:
        if client is None:
            from google.cloud import firestore  # optional dependency

            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            if not project:
                raise ValueError("GOOGLE_CLOUD_PROJECT must be set for STATE_BACKEND=firestore")
            client = firestore.Client(
                project=project,
                database=database or os.getenv("FIRESTORE_DATABASE", "small-business-loan-states"),
            )
        self.db = client

    def _ref(self, request_id: str):
        return self.db.collection(self.collection).document(request_id)

    def _load(self, request_id: str) -> dict | None:
        snap = self._ref(request_id).get()
        return _json_safe(snap.to_dict()) if snap.exists else None

    def _save(self, request_id: str, doc: dict) -> None:
        self._ref(request_id).set(doc)

    def delete(self, request_id: str) -> None:
        self._ref(request_id).delete()


_STORE: dict[str, StateStore] = {}


def get_state_store() -> StateStore:
    """Process-wide store for the configured STATE_BACKEND."""
    backend = (os.getenv("STATE_BACKEND") or "memory").strip().lower()
    if backend not in _STORE:
        if backend == "memory":
            _STORE[backend] = MemoryStateStore()
        elif backend == "local":
            _STORE[backend] = MemoryStateStore(os.getenv("LOCAL_STATE_PATH") or ".loan_state.json")
        elif backend == "firestore":
            _STORE[backend] = FirestoreStateStore()
        else:
            raise ValueError(f"Unknown STATE_BACKEND={backend!r}: use memory, local or firestore")
    return _STORE[backend]
