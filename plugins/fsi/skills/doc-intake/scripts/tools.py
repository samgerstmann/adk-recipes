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

"""Orchestrator tools. Deterministic except the two Gemini calls in extraction."""

import asyncio
import logging
import re
from pathlib import Path

from google.adk.tools.tool_context import ToolContext

from . import extraction, intake, output, packs
from .state_store import STATUS_APPROVED, get_state_store

logger = logging.getLogger(__name__)


def package_id_for(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", Path(filename).stem)[:60]


def _current(tool_context: ToolContext) -> tuple[str | None, dict | None]:
    package_id = tool_context.state.get("package_id")
    return package_id, (get_state_store().get(package_id) if package_id else None)


def _mark_changed(doc: dict, tool_context: ToolContext) -> None:
    """Approval must come in a later turn than the latest change."""
    doc["last_change_invocation"] = tool_context.invocation_id


def list_documents() -> dict:
    """List the active domain pack and the document packages available to process.

    Returns:
        dict: pack name, document types, and package file names.
    """
    try:
        pack = packs.load_pack()
        files = packs.list_document_files()
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {
        "status": "success",
        "pack": pack["name"],
        "pack_description": pack["description"],
        "doc_types": list(pack["doc_types"]),
        "available_packs": packs.list_packs(),
        "source": packs.documents_source(),
        "packages": files,
    }


async def process_package(filename: str, tool_context: ToolContext, reprocess: bool = False) -> dict:
    """Split a document package, extract fields, assign review lanes and run checks.

    If the package was already processed, returns its saved state (resume)
    unless reprocess is true.

    Args:
        filename: Package file name from list_documents.
        reprocess: Re-run splitting and extraction even if state exists.

    Returns:
        dict: documents with page ranges, lane counts, review queue and failed checks.
    """
    name = Path(filename.strip()).name
    package_id = package_id_for(name)
    tool_context.state["package_id"] = package_id
    tool_context.state["filename"] = name
    store = get_state_store()
    existing = store.get(package_id)
    if existing and not reprocess:
        return {"status": "resumed", "message": "Loaded saved review state.", **intake.summary(existing)}
    try:
        pack = packs.load_pack()
        data, mime = packs.load_document(name)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    texts = extraction.page_texts(data, mime)
    page_count = len(texts)
    doc_type_names = list(pack["doc_types"])
    documents, problems = [], ["not split"]
    for _ in range(2):  # one retry if the page ranges do not partition the package
        raw = await extraction.classify_and_split(data, mime, page_count, pack["doc_types"])
        documents = [
            {"index": i, "doc_type": d["doc_type"], "start_page": int(d["start_page"]),
             "end_page": int(d["end_page"]), "confidence": round(float(d["confidence"]), 3)}
            for i, d in enumerate(sorted(raw, key=lambda d: d["start_page"]), start=1)
        ]
        problems = extraction.validate_split(documents, page_count, doc_type_names)
        if not problems:
            break
        logger.warning("Split problems for %s: %s", name, problems)

    async def extract(d: dict) -> tuple[int, list[dict]]:
        part = extraction.slice_pdf(data, d["start_page"], d["end_page"]) if mime == "application/pdf" else data
        return d["index"], await extraction.extract_fields(part, mime, d["doc_type"], pack["doc_types"][d["doc_type"]])

    targets = [d for d in documents if d["doc_type"] in pack["doc_types"]] if not problems else []
    extracted = dict(await asyncio.gather(*(extract(d) for d in targets)))
    doc = intake.build_package(
        package_id, name, pack, page_count, documents, extracted, texts,
        extraction.injection_warnings(texts), problems,
    )
    _mark_changed(doc, tool_context)
    store.save(package_id, doc)
    return {"status": "processed", **intake.summary(doc)}


def check_status(tool_context: ToolContext) -> dict:
    """Return the saved state of the current package (resume after a restart).

    Returns:
        dict: status, review queue and failed checks.
    """
    package_id, doc = _current(tool_context)
    if not doc:
        return {"status": "error", "message": f"No saved state for {package_id or 'this session'}. Process a package first."}
    return {"status": "found", **intake.summary(doc)}


def get_field_details(tool_context: ToolContext, document_index: int = 0) -> dict:
    """All extracted fields (value, page, quote, confidence, lane, status).

    Args:
        document_index: Document to show; 0 = all documents.

    Returns:
        dict: fields per document.
    """
    _, doc = _current(tool_context)
    if not doc:
        return {"status": "error", "message": "No package in this session."}
    out = {}
    for d in doc["documents"]:
        if document_index and d["index"] != document_index:
            continue
        out[f"{d['index']}:{d['doc_type']}"] = {
            name: {k: f[k] for k in ("value", "page", "quote", "confidence", "lane", "status")}
            for name, f in doc["fields"].get(str(d["index"]), {}).items()
        }
    return {"status": "success", "fields": out}


def correct_field(document_index: int, field_name: str, value: str, tool_context: ToolContext) -> dict:
    """Apply a correction typed by the HUMAN reviewer (value must be in their message).

    Args:
        document_index: Document index from the summary.
        field_name: Field to correct.
        value: The value the human typed.

    Returns:
        dict: updated status, review queue and failed checks.
    """
    package_id, doc = _current(tool_context)
    if not doc:
        return {"status": "error", "message": "No package in this session."}
    if doc["status"] == STATUS_APPROVED:
        return {"status": "error", "message": "Record already approved; reset to change it."}
    try:
        doc = intake.apply_correction(doc, packs.load_pack(), int(document_index), field_name, value, tool_context.user_id)
    except KeyError as e:
        return {"status": "error", "message": str(e)}
    _mark_changed(doc, tool_context)
    get_state_store().save(package_id, doc)
    return {"status": "corrected", **intake.summary(doc)}


def accept_fields(tool_context: ToolContext, document_index: int = 0, field_names: list[str] | None = None) -> dict:
    """Reviewer confirms pending (review-lane) values as extracted.

    Args:
        document_index: Limit to one document; 0 = all documents.
        field_names: Limit to these fields; empty = all pending fields.

    Returns:
        dict: accepted fields and the remaining review queue.
    """
    package_id, doc = _current(tool_context)
    if not doc:
        return {"status": "error", "message": "No package in this session."}
    accepted = intake.accept_fields(doc, packs.load_pack(), tool_context.user_id, document_index or None, field_names or None)
    _mark_changed(doc, tool_context)
    get_state_store().save(package_id, doc)
    return {"status": "accepted", "accepted": accepted, **intake.summary(doc)}


def approve_record(tool_context: ToolContext, reviewer_note: str = "") -> dict:
    """Write the human-approved structured record (enforced human approval).

    Args:
        reviewer_note: Reviewer note; required when checks failed (taken from the human's message).

    Returns:
        dict: where the record was written.
    """
    package_id, doc = _current(tool_context)
    if not doc:
        return {"status": "error", "message": "No package in this session."}
    approval = doc.get("approval") or {}
    if not approval.get("approved_by"):
        return {"status": "error", "message": "Human approval required: no approval on record."}
    record = intake.build_record(doc)
    try:
        written = output.write_record(record)
    except Exception as e:
        return {"status": "error", "message": f"Approved, but writing the record failed: {e!s}"}
    doc["output"] = written
    get_state_store().save(package_id, doc)
    return {"status": "approved", "record": written, "approved_by": approval["approved_by"],
            "reviewer_note": approval.get("reviewer_note", ""), "advisory_notice": intake.ADVISORY}


def reset_package(tool_context: ToolContext) -> dict:
    """Delete the saved state of the current package (memory/local backends only).

    Returns:
        dict: reset result.
    """
    package_id = tool_context.state.get("package_id")
    store = get_state_store()
    if not package_id:
        return {"status": "error", "message": "No package in this session."}
    if store.backend == "firestore":
        return {"status": "error", "message": "Reset is disabled for the Firestore backend."}
    store.delete(package_id)
    return {"status": "success", "message": f"State of {package_id} deleted."}
