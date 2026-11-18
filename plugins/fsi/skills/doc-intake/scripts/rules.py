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

"""Review lanes and the declarative consistency-check engine (pure code).

Rule types (rules.yaml):
- required                      every schema field with "required": true has a value
- compare  left op right        op in == != < <= > >=; right is a field ref or a
                                number; optional factor (scales right),
                                tolerance_pct / tolerance_pct_from (field ref)
- date_before left right        left date before right (inclusive: true = on or before)
- equals   left right           normalized text equality

Field refs are `<doc_type>.<field>`; the first document of a type is used.
Rules whose inputs are missing are `skipped` (the `required` rule reports
missing values).
"""

import operator
import os
import re
from datetime import date, datetime

LANE_AUTO, LANE_REVIEW, LANE_REJECT = "auto", "review", "reject"
_OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d")


def thresholds() -> tuple[float, float]:
    return float(os.getenv("TAU_HIGH") or 0.9), float(os.getenv("TAU_LOW") or 0.5)


def is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def assign_lane(confidence: float | None, value, required: bool, tau_high: float, tau_low: float) -> str:
    """auto (trust) / review (human checks) / reject (human must enter)."""
    if is_empty(value):
        return LANE_REJECT if required else LANE_AUTO
    conf = float(confidence or 0.0)
    if conf >= tau_high:
        return LANE_AUTO
    if conf >= tau_low:
        return LANE_REVIEW
    return LANE_REJECT


def parse_number(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if is_empty(value):
        return None
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if is_empty(value):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return datetime.strptime(match.group(0), "%Y-%m-%d").date() if match else None


def normalize_text(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def effective_value(field: dict):
    return field.get("value")


def _first_docs(documents: list[dict], fields: dict) -> dict:
    """{doc_type: {field_name: field_record}} using the first doc of each type."""
    out: dict = {}
    for doc in documents:
        if doc["doc_type"] not in out:
            out[doc["doc_type"]] = {"_index": doc["index"], **fields.get(str(doc["index"]), {})}
    return out


def _lookup(by_type: dict, ref) -> tuple[object, dict | None]:
    if not isinstance(ref, str):
        return ref, None
    doc_type, _, name = ref.partition(".")
    record = by_type.get(doc_type, {}).get(name)
    if record is None:
        return None, None
    evidence = {
        "ref": ref,
        "document_index": by_type[doc_type]["_index"],
        "value": record.get("value"),
        "page": record.get("page"),
    }
    return record.get("value"), evidence


def _result(rule: dict, status: str, details: str, evidence: list) -> dict:
    return {
        "id": rule["id"],
        "type": rule["type"],
        "severity": rule.get("severity", "medium"),
        "status": status,
        "message": rule.get("message", rule["id"]) if status == "fail" else details,
        "details": details,
        "evidence": [e for e in evidence if e],
    }


def evaluate_rules(rules: list[dict], doc_types: dict, documents: list[dict], fields: dict) -> list[dict]:
    """Run the pack's checks over the current field values."""
    by_type = _first_docs(documents, fields)
    results = []
    for rule in rules:
        kind = rule["type"]
        if kind == "required":
            for doc in documents:
                spec = doc_types.get(doc["doc_type"], {})
                doc_fields = fields.get(str(doc["index"]), {})
                missing = [
                    f["name"] for f in spec.get("fields", [])
                    if f.get("required") and is_empty(doc_fields.get(f["name"], {}).get("value"))
                ]
                evidence = [{"ref": f"{doc['doc_type']}.{m}", "document_index": doc["index"], "value": "", "page": None} for m in missing]
                status = "fail" if missing else "pass"
                result = _result(rule, status, f"{doc['doc_type']} (document {doc['index']}): missing {', '.join(missing) or 'none'}", evidence)
                if missing:
                    result["message"] = f"Missing required field(s) in {doc['doc_type']}: {', '.join(missing)}"
                results.append(result)
            continue
        left, ev_left = _lookup(by_type, rule.get("left"))
        right, ev_right = _lookup(by_type, rule.get("right"))
        evidence = [ev_left, ev_right]
        if is_empty(left) or is_empty(right):
            results.append(_result(rule, "skipped", "input value missing", evidence))
            continue
        if kind == "compare":
            lnum, rnum = parse_number(left), parse_number(right)
            if lnum is None or rnum is None:
                results.append(_result(rule, "skipped", f"not numeric: {left!r} / {right!r}", evidence))
                continue
            rnum *= float(rule.get("factor", 1))
            tol_pct = float(rule.get("tolerance_pct", 0) or 0)
            if rule.get("tolerance_pct_from"):
                tol_value, ev_tol = _lookup(by_type, rule["tolerance_pct_from"])
                if parse_number(tol_value) is not None:
                    tol_pct = abs(parse_number(tol_value))
                    evidence.append(ev_tol)
            op = rule.get("op", "==")
            tol = abs(rnum) * tol_pct / 100
            if op == "==":
                ok = abs(lnum - rnum) <= tol
            elif op in ("<=", "<"):
                ok = _OPS[op](lnum, rnum + tol)
            elif op in (">=", ">"):
                ok = _OPS[op](lnum, rnum - tol)
            else:
                ok = _OPS[op](lnum, rnum)
            limit = f"{rnum:,.2f}" + (f" (+/- {tol_pct:g}% = {tol:,.2f})" if tol_pct else "")
            results.append(_result(rule, "pass" if ok else "fail", f"{lnum:,.2f} {op} {limit}", evidence))
        elif kind == "date_before":
            ldate, rdate = parse_date(left), parse_date(right)
            if ldate is None or rdate is None:
                results.append(_result(rule, "skipped", f"not a date: {left!r} / {right!r}", evidence))
                continue
            ok = ldate <= rdate if rule.get("inclusive", True) else ldate < rdate
            results.append(_result(rule, "pass" if ok else "fail", f"{ldate} {'<=' if rule.get('inclusive', True) else '<'} {rdate}", evidence))
        elif kind == "equals":
            ok = normalize_text(left) == normalize_text(right)
            results.append(_result(rule, "pass" if ok else "fail", f"{left!r} vs {right!r}", evidence))
    return results
