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

"""State backends (memory, local JSON, Firestore via a fake client)."""

import pytest

from scripts import state_store as ss


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


def test_save_get_delete(store):
    assert store.get("pkg") is None
    saved = store.save("pkg", {"package_id": "pkg", "status": "needs_review", "fields": {"1": {"x": {"value": "a"}}}})
    assert saved["updated_at"]
    doc = store.get("pkg")
    doc["fields"]["1"]["x"]["value"] = "mutated"  # copies, not references
    assert store.get("pkg")["fields"]["1"]["x"]["value"] == "a"
    store.delete("pkg")
    assert store.get("pkg") is None


def test_local_backend_persists_across_instances(tmp_path):
    path = tmp_path / "state.json"
    ss.MemoryStateStore(path).save("pkg", {"package_id": "pkg"})
    assert ss.MemoryStateStore(path).get("pkg")["package_id"] == "pkg"


def test_get_state_store_selects_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_STORE", {})
    monkeypatch.setenv("STATE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "s.json"))
    assert ss.get_state_store().backend == "local"
    monkeypatch.setenv("STATE_BACKEND", "bogus")
    with pytest.raises(ValueError):
        ss.get_state_store()
