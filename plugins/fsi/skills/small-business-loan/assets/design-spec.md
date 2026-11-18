---
# Small Business Loan Agent - Design Spec
# The Q-MODE interview edits these values; setup.py reads them.

# --- Required ---
gcp_project_id: ""                     # REQUIRED: GCP project for Gemini on Vertex AI
gcp_region: global                     # Vertex AI location (global or e.g. us-central1, europe-west4)

# --- Applications ---
applications_source: bundled           # bundled | local folder of PDFs/images | gs://bucket[/prefix]

# --- Workflow state (repair & resume) ---
state_backend: memory                  # memory (Quick start) | local (JSON file) | firestore (existing DB)
firestore_database: small-business-loan-states   # only for state_backend: firestore

# --- Underwriting ---
eligibility_rules: bundled             # bundled | path to a rules JSON shaped like assets/eligibility_rules.json
---

# Small Business Loan Agent

## Overview

Configuration of a small business loan processing agent: an orchestrator
runs document extraction, underwriting and pricing, presents a summary, and
finalizes the decision only after a human loan officer explicitly approves.
Incomplete applications pause for a human repair and resume from the
checkpoint.

## How to Use

1. Let the coding assistant fill the values during Q-MODE (or edit them).
2. Run `.venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md`.
3. Start `.venv/bin/adk web "$SKILL_DIR/scripts" --port 8765`.

## Design Decisions

- **Enforced human approval**: `LoanDecisionAgent` is blocked by a
  before-tool gate unless the summary was shown in an earlier turn and the
  human's latest message is an explicit approval. Requests like "approve it
  yourself" are refused. The approval is recorded in the workflow state.
- **Repair & resume**: missing critical fields pause the workflow; a human
  supplies the value in chat (it must appear in their message) or edits the
  state document; processing resumes from the next step.
- **Pluggable state**: `memory` needs no cloud resources; `firestore` keeps
  the original sample's durable, multi-instance design.
- **Data residency**: choose a regional `gcp_region` for regulated data.
