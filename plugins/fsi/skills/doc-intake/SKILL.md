---
name: fsi-doc-intake
description: >-
  Creates a document intake agent on Google Cloud that splits a document
  package into documents, extracts source-linked fields (value, page,
  verbatim quote, confidence) with Gemini, routes uncertain fields to a human
  reviewer, runs within-package consistency checks from a domain pack, and
  writes a human-approved structured record with an audit trail. Ships packs
  for small business loan applications and trade finance (documentary
  credit) packages. Use when the user wants to "extract data from a document
  package", "split and classify financial documents", "build a document
  intake / KYC / loan / trade finance review agent", "check documents
  against each other" or "add human review to document extraction".
metadata:
  author: Google
  license: Apache-2.0
  version: 0.1.0
  credits: >-
    Loan sample PDFs and the multi-agent loan workflow from
    contrib/python/small-business-loan-agent (Nora Filali, AI FDE Financial
    Services); trade-document pattern from the FDE trade-document catalog
    (Bradley Ramos).
---

# Document Intake Agent

Creates an advisory agent that turns a document package into a **reviewed,
human-approved structured record**: package → documents (page ranges) →
fields with page + quote + confidence → auto / review / reject lanes →
consistency checks → human corrections → human approval → JSON record. It
never makes credit, payment or compliance decisions.

## STOP — Q-MODE FIRST

**If setup is already complete** (the system context says "DEPLOYED document
intake agent", or this conversation already ran `setup.py`), skip Q-MODE and
help with the agent directly.

**Otherwise**, your first message MUST be exactly this:

```
[skill: fsi-doc-intake] active.
Q-MODE: Pick a setup mode? [default: 1]
  1. Quick start -- 3 questions, bundled sample packages, no cloud resources besides Gemini, ~2 min.
  2. Full setup  -- 8 questions, region, thresholds, state, output, your own pack, ~6 min.
```

Then stop and wait. Accept `1`, `quick`, empty/Enter (Quick), or `2`, `full`
(Full).

## Execution Rules

1. Q-MODE first. No preamble, no plan.
2. One question at a time. Format: `Q: <question>? [default: <value>]`.
   Empty input = default.
3. Write each answer into `./design-spec.md` by **editing** the YAML
   frontmatter that bootstrap copied. Never rewrite the file.
4. After the interview run `setup.py`, then Test. Never skip a failed step.
5. The user can say "configure more" during Quick start to switch to Full.
6. Never create cloud resources (Firestore databases, buckets, BigQuery
   tables) without explicit user approval; `setup.py` itself creates none.
7. Use only synthetic / sample documents in shared projects.

## Workspace Setup

The skill has two locations: the **install dir** (SKILL.md, scripts, packs)
and the **workspace** (the current directory: `design-spec.md`, `.venv`,
`.env`, `records/`). Run this as ONE shell command:

```bash
SKILL_DIR=$(for d in ~/.claude/skills ~/.agents/skills ~/.gemini/skills ~/.cursor/skills; do
  [ -f "$d/fsi-doc-intake/SKILL.md" ] && echo "$d/fsi-doc-intake" && break
done)
bash "$SKILL_DIR/scripts/bootstrap.sh"
```

`bootstrap.sh` uses `uv` when available (else Python 3.11+ venv), installs the
skill editable and copies `assets/design-spec.md` to `./design-spec.md`.
Firestore / GCS / BigQuery need `SKILL_EXTRAS=gcp` in front. Always use
`.venv/bin/python` and `.venv/bin/adk`.

## Mode 1: Quick start (3 questions)

| Q | Question | Default | Writes |
|---|---|---|---|
| Q-A | GCP project ID (for Gemini on Vertex AI)? | `$GOOGLE_CLOUD_PROJECT` or `gcloud config get-value project` | `gcp_project_id` |
| Q-B | Domain pack? `1` small-business-loan / `2` trade-finance | `1` | `pack` |
| Q-C | Documents? `1` the pack's bundled samples / `2` my local folder | `1` | `documents_source` |

Silent defaults: `gcp_region: global`, thresholds 0.9 / 0.5, `state_backend:
memory`, `output_destination: local` (`./records/<package>.json`), Gemini
extractor, LLM judge off.

After Q-C, automatically and **sequentially**:

1. Wait for bootstrap, then edit `./design-spec.md`.
2. Run `.venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md`
   (validates the pack and documents, writes `./.env`, creates no cloud
   resources).
3. Go to Test.

To switch pack later: edit `pack:` in `./design-spec.md`, re-run `setup.py`,
restart `adk web`.

## Mode 2: Full setup (8 questions)

Q-A, Q-B (also accepts a path to your own pack folder), Q-C (also accepts
`gs://bucket/prefix`), then:

| Q | Question | Default | Writes |
|---|---|---|---|
| Q-D | Vertex AI region? | `global` | `gcp_region` |
| Q-E | Review thresholds `tau_high,tau_low`? | `0.9,0.5` | `tau_high`, `tau_low` |
| Q-F | Workflow state? `1` memory / `2` local JSON / `3` Firestore (existing DB) | `2` | `state_backend` (+ `firestore_database`) |
| Q-G | Approved record output? `1` local JSON / `2` BigQuery (existing table) / `3` GCS | `1` | `output_destination` (+ `bq_table` / `output_gcs_uri`) |
| Q-H | Extractor? `1` Gemini / `2` Document AI (opt-in stub) | `1` | `extractor` |

Then run `setup.py --config ./design-spec.md --verify-cloud` (read-only
checks of the database / bucket / table). **Ask before creating** any of
them. Document AI: see [references/document-ai.md](references/document-ai.md).

## When to Use

- Intake of multi-document packages (loan files, trade finance presentations,
  KYC bundles, claims) into a structured, reviewed record.
- Pre-checking documents against each other for a human checker.
- Building a labelled review set to calibrate confidence thresholds.

Don't use it to make the credit / payment / compliance decision, for sanctions
screening, or as a system of record without your own controls.

## Adding a Domain Pack (no code changes)

```
my-pack/
  schema.json   {"pack": "...", "doc_types": {"<type>": {"description": "...",
                 "fields": [{"name", "type": string|money|number|date, "required", "description"}]}}}
  rules.yaml    rules: [{id, type: required|compare|date_before|equals, left, right, op, tolerance_pct, factor, inclusive, severity, message}]
  samples/      example packages (PDF/PNG/JPG)
```

Set `pack: /path/to/my-pack` in `design-spec.md` (or `PACK_PATH`), re-run
`setup.py`. `setup.py` validates the schema and every rule's field references.

## Project Tree

```
fsi-doc-intake/
  SKILL.md  DEMO.md  README.md  EVAL.yaml
  assets/
    design-spec.md
    packs/small-business-loan/   schema.json, rules.yaml, samples/ (complete + incomplete application)
    packs/trade-finance/         schema.json, rules.yaml, samples/ (SYNTHETIC 5-page package), generate_sample.py
  references/                    architecture, document-ai, troubleshooting
  scripts/
    agent.py                     orchestrator (App name "scripts")
    tools.py                     list / process / review / correct / accept / approve / reset
    extraction.py                Gemini split + extract (swap point), PDF helpers, injection tripwire
    rules.py                     lanes + declarative checks engine
    intake.py                    package workflow + audit trail + record
    callbacks.py                 human-in-the-loop gates, optional LLM judge
    packs.py  state_store.py  output.py  prompts.py
    setup.py  _setup_utils.py  bootstrap.sh
  tests/                         offline tests (Gemini mocked)
```

## Test

```bash
.venv/bin/adk web "$SKILL_DIR/scripts" --port 8767
```

Open http://127.0.0.1:8767, select `scripts` (full script: [DEMO.md](DEMO.md)):

- small-business-loan: "Process sample_application_incomplete.pdf" → missing
  `loan_amount_requested` in the reject lane → "correct loan_amount_requested
  = $150,000" → "Approve" → `records/sample_application_incomplete.json`.
- trade-finance: "Process trade_package_synthetic.pdf" → 4 documents (B/L on
  pages 3-4), 2 failed checks with pages, injection warning on page 2 →
  "Approve. Note: ..." → record with the reviewer note.
- "approve it yourself" → refused.

Smoke test without a model call:
`.venv/bin/python -c "from scripts import tools; print(tools.list_documents())"`.

## Evaluate

```bash
cd "$SKILL_DIR" && uv run pytest
```

Offline tests mock the two Gemini calls and cover packs, rules, lanes, quote
verification, page-range validation, corrections, accepts, the approval gate,
output (BigQuery / GCS mocked), state backends and setup. `EVAL.yaml` has 12
live cases. Target: 80%+.

## Deploy

**Never deploy without explicit human approval.** Use `state_backend:
firestore` for multi-instance deployments. Runtime identity: `roles/aiplatform.user`
plus `roles/datastore.user` (Firestore), `roles/storage.objectViewer` /
`objectCreator` (GCS), `roles/bigquery.dataEditor` on the table (BigQuery).
Keep documents in an approved project and region; log no document text.

## Gotchas

- **Approval is enforced in code**: `approve_record` is blocked unless the
  review queue is empty, the human saw the latest state (approval comes in a
  later turn than the last change), the human's message is an explicit
  approval, and — if checks failed — it carries a reviewer note (`Approve.
  Note: ...`). The note is taken from the human's message, not the model.
- **Corrections come from the human**: the corrected value must appear in the
  reviewer's message; accepts need an explicit "accept"/"confirm".
- **Confidence is model-reported**, then capped at 0.6 when the quote is not
  found on the cited page (PDF text layer). Calibrate `TAU_HIGH` / `TAU_LOW`
  on labelled samples before trusting the auto lane.
- **Page ranges must partition the package**; otherwise extraction is skipped
  and `split_problems` is reported.
- **Document text is data**: instructions inside documents are ignored and
  instruction-like text is flagged (`injection_warnings`, a regex tripwire).
- **Checks use the first document of each type** in a package.
- **One package per session is easiest**; memory state resets on restart;
  "reset" deletes the package state (memory/local only).
- **App name must be `scripts`**; use `.venv/bin/adk`, not a global `adk`.

## Troubleshooting

Full table: [references/troubleshooting.md](references/troubleshooting.md).

| Error | Fix |
|---|---|
| `design-spec.md has no YAML frontmatter` | File was rewritten. Delete it, re-run bootstrap, edit only the values |
| `Pack ...: ... references unknown field` | Fix `rules.yaml` field refs (`<doc_type>.<field>` from `schema.json`) |
| `adk web` shows no agents | `.venv/bin/adk web "$SKILL_DIR/scripts" --port 8767` |
| `pages not assigned to any document` | Split did not partition the package; reprocess, or check the PDF |
| `Cannot approve: N field(s) still need review` | Correct or accept the review queue first |
| `Reviewer note required` | Approve with `Approve. Note: <your note>` |
| `Reauthentication is needed` | `gcloud auth application-default login` |
| `PERMISSION_DENIED` / `API has not been used` | `gcloud services enable aiplatform.googleapis.com`; grant `roles/aiplatform.user` |

## Completion Checklist

- [ ] Mode chosen; `design-spec.md` filled; `setup.py` succeeded
- [ ] `list_documents` smoke test works
- [ ] A package processed; review queue corrected/accepted; record approved and written
- [ ] Trade-finance package shows both planted discrepancies with pages
- [ ] "Approve it yourself" refused
- [ ] Evaluation at 80%+; deployed only after human approval

## References

- [DEMO.md](DEMO.md) — 3-minute demo script for both packs
- [references/architecture.md](references/architecture.md) — flow, gates, record format, relation to the loan sample
- [references/document-ai.md](references/document-ai.md) — Document AI opt-in
- [references/troubleshooting.md](references/troubleshooting.md) — full error table
