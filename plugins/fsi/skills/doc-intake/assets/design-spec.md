---
# Document Intake Agent - Design Spec
# The Q-MODE interview edits these values; setup.py reads them.

# --- Required ---
gcp_project_id: ""                     # REQUIRED: GCP project for Gemini on Vertex AI
gcp_region: global                     # Vertex AI location (global or e.g. us-central1, europe-west4)

# --- Domain pack & documents ---
pack: small-business-loan              # small-business-loan | trade-finance | path to your own pack folder
documents_source: bundled              # bundled (pack samples/) | local folder | gs://bucket[/prefix]

# --- Review lanes ---
tau_high: 0.9                          # confidence >= tau_high: auto
tau_low: 0.5                           # tau_low <= confidence < tau_high: review; below: reject

# --- Workflow state (review, repair & resume) ---
state_backend: memory                  # memory (Quick start) | local (JSON file) | firestore (existing DB)
firestore_database: "(default)"        # only for state_backend: firestore

# --- Approved record output ---
output_destination: local              # local (./records) | bigquery | gcs
bq_table: ""                           # project.dataset.table (existing) for bigquery
output_gcs_uri: ""                     # gs://bucket/prefix for gcs

# --- Extraction ---
extractor: gemini                      # gemini (default) | documentai (opt-in stub)
---

# Document Intake Agent

## Overview

Configuration of an advisory document intake agent: it splits a package into
documents, extracts the pack's fields with page, verbatim quote and
confidence, sorts them into auto / review / reject lanes, runs the pack's
consistency checks, lets a human reviewer correct or accept uncertain values,
and writes the record only after the human explicitly approves it. It makes
no credit, payment or compliance decisions.

## How to Use

1. Let the coding assistant fill the values during Q-MODE (or edit them).
2. Run `.venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md`.
3. Start `.venv/bin/adk web "$SKILL_DIR/scripts" --port 8767`.

## Design Decisions

- **Packs, not code**: document types, fields and checks live in
  `schema.json` + `rules.yaml`; a new domain is a new folder.
- **Source-linked fields**: every value carries page + verbatim quote; quotes
  not found on the page cap confidence so the field goes to review.
- **Deterministic guard rails**: page ranges must partition the package;
  checks are plain code; corrections and approval are gated in code.
- **Human approval enforced**: the record is written only after an explicit
  human approval in a later turn than the last change, with a reviewer note
  when checks failed.
- **Data residency**: choose a regional `gcp_region` for regulated documents.
