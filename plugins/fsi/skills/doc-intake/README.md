# fsi-doc-intake

Agent Plugins skill that builds an **advisory document intake agent** with
the Agent Development Kit (ADK) and Gemini on Vertex AI:

> An agent that splits a document package, extracts source-linked fields with
> confidence scores and has a reviewer correct the uncertain ones — plus
> within-package consistency checks. It produces a reviewed, human-approved
> structured record; it does not make credit, loan or payment decisions.

- **Split** a package into documents (page ranges validated in code).
- **Extract** each document type's fields with value, page, verbatim quote and
  confidence (Gemini, JSON schema, temperature 0; one swap point for your own
  reader, e.g. Document AI).
- **Lanes**: auto / review / reject by `TAU_HIGH` / `TAU_LOW`; quotes not found
  on the cited page are capped into review.
- **Consistency checks** from a declarative `rules.yaml` (required, compare
  with tolerance/factor, date_before, equals).
- **Human review**: corrections (value must be typed by the human), accepts,
  **enforced human approval** (reviewer note required when checks fail), audit
  trail (who / when / what changed).
- **Record** written as JSON locally (Quick start) or to BigQuery / GCS (Full).
- Pluggable state (`memory` / `local` / `firestore`) for repair & resume,
  prompt-injection tripwire, optional LLM judge.

See [SKILL.md](SKILL.md) (Q-MODE installer), [DEMO.md](DEMO.md) (3-minute
demo for both packs), [references/architecture.md](references/architecture.md).

## Domain packs

| Pack | Documents | Checks | Sample |
|---|---|---|---|
| `small-business-loan` (default) | loan application | required fields, amount > 0, amount ≤ 75% of revenue, term ≤ 120 months, ≥ 1 year operating | `sample_application_complete.pdf`, `sample_application_incomplete.pdf` (missing loan amount) |
| `trade-finance` | commercial invoice, packing list, bill of lading, letter of credit | invoice ≤ LC amount + tolerance, shipment ≤ latest shipment date / expiry, currency / LC number / invoice number / port / weight consistency | `trade_package_synthetic.pdf` — SYNTHETIC, 5 pages, 2 planted discrepancies + an injected instruction |

### Add a pack without code changes

```bash
mkdir -p ~/packs/kyc/samples && cp my_id_scans/*.pdf ~/packs/kyc/samples/
cat > ~/packs/kyc/schema.json <<'EOF'
{"pack": "kyc", "description": "Customer identification bundle",
 "doc_types": {
   "id_document": {"description": "Passport or national ID card", "fields": [
     {"name": "full_name", "type": "string", "required": true, "description": "Holder name"},
     {"name": "date_of_birth", "type": "date", "required": true, "description": "Date of birth"},
     {"name": "expiry_date", "type": "date", "required": true, "description": "Document expiry"}]},
   "proof_of_address": {"description": "Utility bill or bank statement", "fields": [
     {"name": "full_name", "type": "string", "required": true, "description": "Addressee"},
     {"name": "statement_date", "type": "date", "required": true, "description": "Issue date"}]}}}
EOF
cat > ~/packs/kyc/rules.yaml <<'EOF'
rules:
  - {id: required_fields, type: required, severity: high}
  - {id: same_person, type: equals, left: id_document.full_name, right: proof_of_address.full_name,
     severity: high, message: Name on proof of address differs from the ID}
  - {id: id_not_expired_at_statement, type: date_before, left: proof_of_address.statement_date,
     right: id_document.expiry_date, severity: medium, message: ID expired before the statement date}
EOF
# design-spec.md:  pack: ~/packs/kyc   then:
.venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md   # validates schema + rule refs
```

## Credits

- **Nora Filali** (POC) and Neil Desai, AI FDE Financial Services — the
  [`contrib/python/small-business-loan-agent`](../../../../contrib/python/small-business-loan-agent/)
  sample: its sample PDFs are the `small-business-loan` pack, and its
  document-extraction stage, human-approval gate and repair & resume pattern
  are what this skill generalizes. That sample also shows the full downstream
  loan workflow (underwriting, pricing, decision), which is out of scope here.
- **Bradley Ramos** — the FDE trade-document extraction pattern (split →
  per-type extraction → cross-document checks → review queue) from his
  catalog, which the `trade-finance` pack follows. The trade sample is newly
  generated and synthetic (`assets/packs/trade-finance/generate_sample.py`).

## Quick start (without a coding assistant)

```bash
SKILL_DIR=$(pwd)/plugins/fsi/skills/doc-intake   # from a repo clone
mkdir -p ~/intake-demo && cd ~/intake-demo
bash "$SKILL_DIR/scripts/bootstrap.sh"
sed -i "s/^gcp_project_id: \"\"/gcp_project_id: $(gcloud config get-value project)/" design-spec.md
.venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md
.venv/bin/adk web "$SKILL_DIR/scripts" --port 8767
```

## Tests

```bash
cd "$SKILL_DIR" && uv run pytest     # offline, Gemini mocked
```

License: Apache-2.0. All sample data is fictional.
