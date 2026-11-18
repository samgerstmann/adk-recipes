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

"""Packs, rules engine and lanes (offline)."""

import json
from datetime import date

import pytest

from scripts import packs
from scripts import rules as R


def test_bundled_packs_load():
    assert packs.list_packs() == ["small-business-loan", "trade-finance"]
    loan = packs.load_pack(packs.PACKS_DIR / "small-business-loan")
    trade = packs.load_pack(packs.PACKS_DIR / "trade-finance")
    assert list(loan["doc_types"]) == ["loan_application"]
    assert set(trade["doc_types"]) == {"commercial_invoice", "packing_list", "bill_of_lading", "letter_of_credit"}
    assert {r["id"] for r in trade["rules"]} >= {"invoice_within_lc_amount", "shipment_by_latest_date"}


def test_pack_selected_by_env(monkeypatch):
    monkeypatch.setenv("PACK", "trade-finance")
    assert packs.list_document_files() == ["trade_package_synthetic.pdf"]
    data, mime = packs.load_document("../../etc/trade_package_synthetic.pdf")  # path stripped
    assert mime == "application/pdf" and data.startswith(b"%PDF")


def write_pack(folder, schema, rules_yaml="rules: []\n"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "schema.json").write_text(json.dumps(schema))
    (folder / "rules.yaml").write_text(rules_yaml)
    return folder


def test_new_pack_needs_no_code(tmp_path, monkeypatch):
    schema = {"pack": "kyc", "doc_types": {"id_card": {"fields": [
        {"name": "full_name", "type": "string", "required": True},
        {"name": "expiry_date", "type": "date", "required": True}]}}}
    rules = "rules:\n  - id: required_fields\n    type: required\n"
    folder = write_pack(tmp_path / "kyc", schema, rules)
    (folder / "samples").mkdir()
    (folder / "samples" / "id.png").write_bytes(b"\x89PNG")
    monkeypatch.setenv("PACK_PATH", str(folder))
    assert packs.load_pack()["name"] == "kyc"
    assert packs.list_document_files() == ["id.png"]


@pytest.mark.parametrize(
    "schema,rules_yaml,error",
    [
        ({"doc_types": {}}, "rules: []", "doc_types"),
        ({"doc_types": {"a": {"fields": [{"name": "x", "type": "blob"}]}}}, "rules: []", "bad field"),
        ({"doc_types": {"a": {"fields": [{"name": "x"}]}}}, "rules:\n  - id: r\n    type: magic\n", "unknown type"),
        ({"doc_types": {"a": {"fields": [{"name": "x"}]}}}, "rules:\n  - id: r\n    type: equals\n    left: a.x\n    right: a.y\n", "unknown field"),
    ],
)
def test_invalid_packs(tmp_path, schema, rules_yaml, error):
    with pytest.raises(packs.PackError, match=error):
        packs.load_pack(write_pack(tmp_path / "p", schema, rules_yaml))


@pytest.mark.parametrize(
    "conf,value,required,lane",
    [(0.95, "x", True, "auto"), (0.7, "x", True, "review"), (0.2, "x", False, "reject"),
     (1.0, "", True, "reject"), (1.0, "", False, "auto")],
)
def test_assign_lane(conf, value, required, lane):
    assert R.assign_lane(conf, value, required, 0.9, 0.5) == lane


def test_parsers():
    assert R.parse_number("USD 52,800.00") == 52800.0
    assert R.parse_number("6,250 kg") == 6250.0
    assert R.parse_number("+/- 5%") == 5.0
    assert R.parse_number("None") is None
    assert R.parse_date("2026-03-18") == date(2026, 3, 18)
    assert R.parse_date("March 15, 2026") == date(2026, 3, 15)
    assert R.parse_date("15.03.2026") == date(2026, 3, 15)
    assert R.parse_date("soon") is None


def f(value, page=1):
    return {"value": value, "page": page}


TRADE_DOCS = [
    {"index": 1, "doc_type": "commercial_invoice"}, {"index": 2, "doc_type": "packing_list"},
    {"index": 3, "doc_type": "bill_of_lading"}, {"index": 4, "doc_type": "letter_of_credit"},
]


def trade_fields(invoice_total="USD 52,800.00", shipped="2026-03-18"):
    return {
        "1": {"invoice_number": f("INV-1"), "invoice_date": f("2026-03-10"), "seller": f("S"), "buyer": f("B"),
              "lc_number": f("LC-1"), "currency": f("USD"), "total_amount": f(invoice_total), "goods_description": f("g")},
        "2": {"invoice_number": f("INV-1", 2), "packages": f("100", 2), "gross_weight_kg": f("6,250 kg", 2), "net_weight_kg": f("", 2)},
        "3": {"bl_number": f("BL", 3), "shipper": f("S", 3), "consignee": f("C", 3), "port_of_loading": f("Valencia, Spain", 3),
              "port_of_discharge": f("Newark", 3), "gross_weight_kg": f("6,250", 4), "shipped_on_board_date": f(shipped, 4)},
        "4": {"lc_number": f("LC-1", 5), "applicant": f("B", 5), "beneficiary": f("S", 5), "currency": f("usd", 5),
              "amount": f("USD 48,000.00", 5), "tolerance_pct": f("5", 5), "latest_shipment_date": f("2026-03-15", 5),
              "expiry_date": f("2026-04-05", 5), "port_of_loading": f("VALENCIA SPAIN", 5), "port_of_discharge": f("", 5)},
    }


def trade_results(fields):
    pack = packs.load_pack(packs.PACKS_DIR / "trade-finance")
    return {r["id"]: r for r in R.evaluate_rules(pack["rules"], pack["doc_types"], TRADE_DOCS, fields)}


def test_trade_rules_flag_both_planted_discrepancies_with_pages():
    results = trade_results(trade_fields())
    amount, ship = results["invoice_within_lc_amount"], results["shipment_by_latest_date"]
    assert amount["status"] == "fail" and "2,400.00" in amount["details"]
    assert {(e["ref"], e["page"]) for e in amount["evidence"]} >= {("commercial_invoice.total_amount", 1), ("letter_of_credit.amount", 5)}
    assert ship["status"] == "fail" and {e["page"] for e in ship["evidence"]} == {4, 5}
    passing = [k for k, r in results.items() if r["status"] == "pass"]
    assert {"currency_matches", "lc_number_matches", "gross_weight_consistent", "port_of_loading_matches",
            "shipment_before_expiry"} <= set(passing)


def test_trade_rules_pass_within_tolerance():
    results = trade_results(trade_fields(invoice_total="USD 50,400.00", shipped="2026-03-15"))
    assert results["invoice_within_lc_amount"]["status"] == "pass"
    assert results["shipment_by_latest_date"]["status"] == "pass"


def test_loan_rules_required_and_skipped():
    pack = packs.load_pack(packs.PACKS_DIR / "small-business-loan")
    docs = [{"index": 1, "doc_type": "loan_application"}]
    fields = {"1": {"business_name": f("Coffee Roasters LLC"), "ein": f("00-1234567"), "years_in_business": f("6"),
                    "owner_name": f("Jane Doe"), "annual_revenue": f("$850,000"), "loan_amount_requested": f(""),
                    "loan_purpose": f("Equipment"), "loan_term_months": f("60")}}
    results = {r["id"]: r for r in R.evaluate_rules(pack["rules"], pack["doc_types"], docs, fields)}
    assert results["required_fields"]["status"] == "fail"
    assert "loan_amount_requested" in results["required_fields"]["message"]
    assert results["requested_amount_positive"]["status"] == "skipped"
    fields["1"]["loan_amount_requested"] = f("$150,000")
    results = {r["id"]: r for r in R.evaluate_rules(pack["rules"], pack["doc_types"], docs, fields)}
    assert all(r["status"] == "pass" for r in results.values())
