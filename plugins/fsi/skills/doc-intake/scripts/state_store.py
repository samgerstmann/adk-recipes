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

"""Package state for review, repair & resume, with pluggable backends.

One JSON document per package (split, fields, checks, review audit trail,
approval). Backends only load and save documents:

- `memory`    -- process-wide dict (Quick start, resets on restart)
- `local`     -- the same dict persisted to a JSON file
- `firestore` -- one Firestore document per package

Selected with STATE_BACKEND (default `memory`).
"""

import copy
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Package statuses
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_READY = "ready_for_approval"
STATUS_APPROVED = "approved"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


class StateStore:
    backend = "base"

    def _load(self, key: str) -> dict | None:
        raise NotImplementedError

    def _save(self, key: str, doc: dict) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def get(self, key: str) -> dict | None:
        doc = self._load(key)
        return copy.deepcopy(_json_safe(doc)) if doc else None

    def save(self, key: str, doc: dict) -> dict:
        doc = _json_safe(copy.deepcopy(doc))
        doc["updated_at"] = now()
        self._save(key, doc)
        return doc


class MemoryStateStore(StateStore):
    """Process-wide dict; optionally persisted to a JSON file (`local`)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.backend = "local" if path else "memory"
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._docs: dict[str, dict] = {}

    def _read(self) -> None:
        if self.path and self.path.exists():
            self._docs = json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def _write(self) -> None:
        if self.path:
            self.path.write_text(json.dumps(self._docs, indent=2), encoding="utf-8")

    def _load(self, key: str) -> dict | None:
        with self._lock:
            self._read()
            return copy.deepcopy(self._docs.get(key))

    def _save(self, key: str, doc: dict) -> None:
        with self._lock:
            self._read()
            self._docs[key] = copy.deepcopy(doc)
            self._write()

    def delete(self, key: str) -> None:
        with self._lock:
            self._read()
            self._docs.pop(key, None)
            self._write()


class FirestoreStateStore(StateStore):
    """One document per package in `doc_intake_packages`."""

    backend = "firestore"
    collection = "doc_intake_packages"

    def __init__(self, database: str | None = None, client: Any = None) -> None:
        if client is None:
            from google.cloud import firestore  # optional dependency ([gcp] extra)

            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            if not project:
                raise ValueError("GOOGLE_CLOUD_PROJECT must be set for STATE_BACKEND=firestore")
            client = firestore.Client(
                project=project, database=database or os.getenv("FIRESTORE_DATABASE", "(default)")
            )
        self.db = client

    def _ref(self, key: str):
        return self.db.collection(self.collection).document(key)

    def _load(self, key: str) -> dict | None:
        snap = self._ref(key).get()
        return _json_safe(snap.to_dict()) if snap.exists else None

    def _save(self, key: str, doc: dict) -> None:
        self._ref(key).set(doc)

    def delete(self, key: str) -> None:
        self._ref(key).delete()


_STORE: dict[str, StateStore] = {}


def get_state_store() -> StateStore:
    """Process-wide store for the configured STATE_BACKEND."""
    backend = (os.getenv("STATE_BACKEND") or "memory").strip().lower()
    if backend not in _STORE:
        if backend == "memory":
            _STORE[backend] = MemoryStateStore()
        elif backend == "local":
            _STORE[backend] = MemoryStateStore(os.getenv("LOCAL_STATE_PATH") or ".doc_intake_state.json")
        elif backend == "firestore":
            _STORE[backend] = FirestoreStateStore()
        else:
            raise ValueError(f"Unknown STATE_BACKEND={backend!r}: use memory, local or firestore")
    return _STORE[backend]
