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

"""Package workflow on plain dicts (pure, unit tested).

build_package -> refresh (lanes, checks, status) -> corrections / accepts by
a human reviewer -> approve -> build_record. Every change is appended to the
package's audit trail (who / when / what).
"""

from . import rules as R
from .extraction import verify_quote
from .state_store import STATUS_APPROVED, STATUS_NEEDS_REVIEW, STATUS_READY, now

UNVERIFIED_CONFIDENCE_CAP = 0.6
ADVISORY = (
    "Advisory intake record: extracted and human-reviewed document data. "
    "It is not a credit, payment or compliance decision."
)


def _audit(doc: dict, who: str, action: str, **details) -> None:
    doc.setdefault("audit", []).append({"at": now(), "who": who, "action": action, **details})


def build_package(
    package_id: str,
    filename: str,
    pack: dict,
    page_count: int,
    documents: list[dict],
    extracted: dict[int, list[dict]],
    texts: list[str],
    warnings: list[dict],
    split_problems: list[str],
) -> dict:
    """Assemble the package document from split + extraction results."""
    tau_high, tau_low = R.thresholds()
    fields: dict = {}
    for d in [] if split_problems else documents:  # no extraction from a bad split
        spec = pack["doc_types"].get(d["doc_type"])
        if not spec:
            continue
        got = {f["name"]: f for f in extracted.get(d["index"], [])}
        out = {}
        for f in spec["fields"]:
            raw = got.get(f["name"], {})
            value = (raw.get("value") or "").strip()
            local_page = int(raw.get("page") or 0)
            page = d["start_page"] + local_page - 1 if 0 < local_page <= d["end_page"] - d["start_page"] + 1 else None
            quote = (raw.get("quote") or "").strip()
            confidence = float(raw.get("confidence") or 0.0)
            verified = verify_quote(quote, texts[page - 1]) if (page and value and page <= len(texts)) else None
            if value and verified is False:
                confidence = min(confidence, UNVERIFIED_CONFIDENCE_CAP)
            lane = R.assign_lane(confidence, value, bool(f.get("required")), tau_high, tau_low)
            out[f["name"]] = {
                "value": value,
                "extracted_value": value,
                "page": page,
                "quote": quote,
                "quote_verified": verified,
                "confidence": round(confidence, 3),
                "lane": lane,
                "required": bool(f.get("required")),
                "status": "auto" if lane == R.LANE_AUTO else "pending",
            }
        fields[str(d["index"])] = out
    doc = {
        "package_id": package_id,
        "filename": filename,
        "pack": pack["name"],
        "page_count": page_count,
        "documents": documents,
        "split_problems": split_problems,
        "injection_warnings": warnings,
        "fields": fields,
        "thresholds": {"tau_high": tau_high, "tau_low": tau_low},
        "review_notes": [],
        "audit": [],
        "approval": None,
        "created_at": now(),
    }
    _audit(doc, "agent", "extracted", documents=len(documents), pages=page_count)
    return refresh(doc, pack)


def open_items(doc: dict) -> list[dict]:
    items = []
    for d in doc["documents"]:
        for name, f in doc["fields"].get(str(d["index"]), {}).items():
            if f["status"] == "pending":
                items.append({
                    "document_index": d["index"], "doc_type": d["doc_type"], "field": name,
                    "value": f["value"], "page": f["page"], "quote": f["quote"],
                    "confidence": f["confidence"], "lane": f["lane"], "quote_verified": f["quote_verified"],
                })
    return items


def failed_checks(doc: dict) -> list[dict]:
    return [c for c in doc.get("checks", []) if c["status"] == "fail"]


def refresh(doc: dict, pack: dict) -> dict:
    doc["checks"] = R.evaluate_rules(pack["rules"], pack["doc_types"], doc["documents"], doc["fields"])
    if doc.get("status") != STATUS_APPROVED:
        doc["status"] = STATUS_NEEDS_REVIEW if (open_items(doc) or doc.get("split_problems")) else STATUS_READY
    return doc


def summary(doc: dict) -> dict:
    """Compact view for the orchestrator (no full field dump)."""
    lanes = {"auto": 0, "review": 0, "reject": 0}
    for doc_fields in doc["fields"].values():
        for f in doc_fields.values():
            lanes[f["lane"]] = lanes.get(f["lane"], 0) + 1
    return {
        "package_id": doc["package_id"],
        "pack": doc["pack"],
        "package_status": doc["status"],
        "page_count": doc["page_count"],
        "documents": [
            {"index": d["index"], "doc_type": d["doc_type"], "pages": f"{d['start_page']}-{d['end_page']}"}
            for d in doc["documents"]
        ],
        "split_problems": doc.get("split_problems", []),
        "lanes": lanes,
        "review_queue": open_items(doc),
        "failed_checks": [
            {"id": c["id"], "severity": c["severity"], "message": c["message"], "details": c["details"],
             "evidence": c["evidence"]}
            for c in failed_checks(doc)
        ],
        "checks_passed": sum(1 for c in doc.get("checks", []) if c["status"] == "pass"),
        "injection_warnings": doc.get("injection_warnings", []),
        "review_notes": doc.get("review_notes", []),
    }


def _field(doc: dict, index: int, name: str) -> dict:
    record = doc["fields"].get(str(index), {}).get(name)
    if record is None:
        raise KeyError(f"No field {name} in document {index}")
    return record


def apply_correction(doc: dict, pack: dict, index: int, name: str, value: str, who: str) -> dict:
    record = _field(doc, index, name)
    old = record["value"]
    record.update(value=value.strip(), status="corrected", corrected_by=who, corrected_at=now())
    _audit(doc, who, "corrected", document_index=index, field=name, old_value=old, new_value=value.strip())
    return refresh(doc, pack)


def accept_fields(doc: dict, pack: dict, who: str, index: int | None = None, names: list[str] | None = None) -> list[str]:
    """Reviewer confirms pending values as extracted. Empty required values cannot be accepted."""
    accepted = []
    for item in open_items(doc):
        if index is not None and item["document_index"] != index:
            continue
        if names and item["field"] not in names:
            continue
        record = _field(doc, item["document_index"], item["field"])
        if R.is_empty(record["value"]) and record["required"]:
            continue
        record.update(status="accepted", accepted_by=who, accepted_at=now())
        accepted.append(f"{item['doc_type']}.{item['field']}")
    if accepted:
        _audit(doc, who, "accepted", fields=accepted)
    refresh(doc, pack)
    return accepted


def approve(doc: dict, who: str, note: str, message: str) -> dict:
    doc["status"] = STATUS_APPROVED
    doc["approval"] = {"approved_by": who, "approved_at": now(), "reviewer_note": note, "human_message": message[:500]}
    if note:
        doc.setdefault("review_notes", []).append({"by": who, "at": now(), "note": note})
    _audit(doc, who, "approved", reviewer_note=note, failed_checks=[c["id"] for c in failed_checks(doc)])
    return doc


def build_record(doc: dict) -> dict:
    documents = []
    for d in doc["documents"]:
        documents.append({
            "index": d["index"],
            "doc_type": d["doc_type"],
            "pages": [d["start_page"], d["end_page"]],
            "fields": {
                name: {k: f.get(k) for k in ("value", "extracted_value", "page", "quote", "quote_verified",
                                              "confidence", "lane", "status")}
                for name, f in doc["fields"].get(str(d["index"]), {}).items()
            },
        })
    return {
        "package_id": doc["package_id"],
        "pack": doc["pack"],
        "source_file": doc["filename"],
        "advisory_notice": ADVISORY,
        "documents": documents,
        "checks": doc.get("checks", []),
        "injection_warnings": doc.get("injection_warnings", []),
        "review_notes": doc.get("review_notes", []),
        "approval": doc["approval"],
        "audit": doc.get("audit", []),
    }
