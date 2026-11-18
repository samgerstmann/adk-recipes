# Financial Services AI Plugin (`plugins/fsi`)

The **Financial Services AI Plugin** bundles Google Cloud skills for financial
services workflows for AI coding assistants (Gemini CLI, Claude Code,
Antigravity, Codex), following the Agent Plugins v1.0.0 layout
(`plugin.json` + `skills/<skill-name>/SKILL.md`):

1. **[`fsi-doc-intake`](./skills/doc-intake/)** — an advisory document intake
   agent: splits a document package, extracts source-linked fields (value,
   page, verbatim quote, confidence) with Gemini, routes uncertain fields to a
   human reviewer, runs within-package consistency checks from a domain pack,
   and writes a human-approved structured record with an audit trail. Ships
   `small-business-loan` and `trade-finance` packs; new domains are a folder
   of `schema.json` + `rules.yaml`, no code. It does not make credit, loan or
   payment decisions.

**Credits:** Nora Filali (POC, `norarfilali`) and Neil Desai (`nmdesai`), AI
FDE Financial Services — sample PDFs and the loan workflow in
[`contrib/python/small-business-loan-agent`](../../contrib/python/small-business-loan-agent/),
whose document-extraction stage this skill generalizes; Bradley Ramos — the
FDE trade-document extraction pattern followed by the trade-finance pack.
**License:** Apache-2.0.

## Skill vs. the deployable loan sample

| Surface | Location | What it covers |
|---|---|---|
| **Agent Plugin Skill** (this folder) | [`skills/doc-intake`](./skills/doc-intake/) | Q-MODE interview → `design-spec.md` → `setup.py` → `adk web`: intake of any pack's document packages into a reviewed record |
| **Standalone sample** | [`contrib/python/small-business-loan-agent`](../../contrib/python/small-business-loan-agent/) | Full downstream loan workflow (extraction → underwriting → pricing → approval → decision) on Agent Engine + Firestore |

## Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- `gcloud` with Application Default Credentials and Vertex AI enabled:
  ```bash
  gcloud auth application-default login
  export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
  ```

## Installation

```bash
npx skills add google/adk-recipes/plugins/fsi --skill fsi-doc-intake
```

Or from a clone: `make -C plugins/fsi/skills/doc-intake install-skill-local`.

## Run

Open your coding assistant in an empty workspace and ask it to "build a
document intake agent"; it runs the Q-MODE setup interview and starts the
agent with `adk web`. A 3-minute demo script for both packs is in
[`skills/doc-intake/DEMO.md`](./skills/doc-intake/DEMO.md). All sample
documents are synthetic or fictional.
