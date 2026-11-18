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

"""Repair & resume state machine across the memory, local and Firestore backends."""

import pytest

from scripts import state_store as ss

RID = "SBL-2025-00391"
COMPLETE = {
    "business_name": "Coffee Roasters LLC",
    "owner_name": "Jane Doe",
    "annual_revenue": "$850,000",
    "loan_amount_requested": "$150,000",
}
INCOMPLETE = {**COMPLETE, "loan_amount_requested": ""}


class FakeFirestore:
    """Minimal stand-in for google.cloud.firestore.Client."""

    def __init__(self):
        self.docs = {}

    def collection(self, name):
        return self

    def document(self, doc_id):
        store = self.docs

        class Snap:
            exists = doc_id in store

            def to_dict(self):
                return store.get(doc_id)

        class Ref:
            def get(self):
                return Snap()

            def set(self, doc):
                store[doc_id] = doc

            def delete(self):
                store.pop(doc_id, None)

        return Ref()


@pytest.fixture(params=["memory", "local", "firestore"])
def store(request, tmp_path):
    if request.param == "memory":
        return ss.MemoryStateStore()
    if request.param == "local":
        return ss.MemoryStateStore(tmp_path / "state.json")
    return ss.FirestoreStateStore(client=FakeFirestore())


def test_new_process_action(store):
    store.create(RID, "s1")
    action = ss.determine_process_action(store.get(RID))
    assert action["action"] == "resume" and action["next_step_to_execute"] == ss.STEP_DOCUMENT_EXTRACTION


def test_incomplete_extraction_pauses_then_repair_resumes(store):
    store.create(RID)
    store.record_step_result(RID, ss.STEP_DOCUMENT_EXTRACTION, INCOMPLETE)
    doc = store.get(RID)
    assert doc["overall_status"] == ss.OVERALL_PENDING_APPROVAL
    assert doc["issues"][0]["missing_fields"] == ["loan_amount_requested"]
    assert not store.can_proceed_to_step(RID, ss.STEP_UNDERWRITING)
    action = ss.determine_process_action(doc)
    assert action["action"] == "pending_approval"
    assert action["open_issue"]["missing_fields"] == ["loan_amount_requested"]
    assert ss.determine_halt_action("UnderwritingAgent", doc["overall_status"], doc["issues"])

    store.apply_repair(RID, "loan_amount_requested", "$150,000", "officer")
    doc = store.get(RID)
    assert doc["overall_status"] == ss.OVERALL_ACTIVE
    assert doc["issues"][0]["resolved"] and doc["issues"][0]["resolved_by"] == "officer"
    assert doc["steps"][ss.STEP_DOCUMENT_EXTRACTION]["repairs"][0]["value"] == "$150,000"
    assert store.can_proceed_to_step(RID, ss.STEP_UNDERWRITING)
    action = ss.determine_process_action(doc)
    assert action["action"] == "resume" and action["next_step_to_execute"] == ss.STEP_UNDERWRITING
    assert action["completed_steps"] == [ss.STEP_DOCUMENT_EXTRACTION]


def test_pricing_completion_waits_for_human(store):
    store.create(RID)
    store.record_step_result(RID, ss.STEP_DOCUMENT_EXTRACTION, COMPLETE)
    store.record_step_result(RID, ss.STEP_UNDERWRITING, {"eligibility_status": "ELIGIBLE"})
    store.record_step_result(RID, ss.STEP_PRICING, {"status": "success"})
    doc = store.get(RID)
    assert doc["overall_status"] == ss.OVERALL_AWAITING_HUMAN
    assert ss.determine_process_action(doc)["action"] == "awaiting_human_approval"
    store.record_human_decision(RID, True, "officer", "yes")
    store.record_step_result(RID, ss.STEP_LOAN_DECISION, {"decision": "APPROVED"})
    doc = store.get(RID)
    assert doc["overall_status"] == ss.OVERALL_COMPLETED
    assert doc["approval"]["decided_by"] == "officer"
    assert ss.determine_process_action(doc)["action"] == "completed"


def test_ineligible_underwriting_pauses(store):
    store.create(RID)
    store.record_step_result(RID, ss.STEP_DOCUMENT_EXTRACTION, COMPLETE)
    store.record_step_result(RID, ss.STEP_UNDERWRITING, {"eligibility_status": "INELIGIBLE"})
    assert store.get(RID)["overall_status"] == ss.OVERALL_PENDING_APPROVAL


def test_decline_and_delete(store):
    store.create(RID)
    store.record_human_decision(RID, False, "officer", "no")
    assert ss.determine_process_action(store.get(RID))["action"] == "declined"
    store.delete(RID)
    assert store.get(RID) is None


def test_local_backend_persists_across_instances(tmp_path):
    path = tmp_path / "state.json"
    ss.MemoryStateStore(path).create(RID)
    assert ss.MemoryStateStore(path).get(RID)["loan_request_id"] == RID


def test_get_state_store_selects_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_STORE", {})
    monkeypatch.setenv("STATE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "s.json"))
    assert ss.get_state_store().backend == "local"
    monkeypatch.setenv("STATE_BACKEND", "bogus")
    with pytest.raises(ValueError):
        ss.get_state_store()
