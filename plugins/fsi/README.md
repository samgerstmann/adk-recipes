# Financial Services AI Plugin (`plugins/fsi`)

The **Financial Services AI Plugin** bundles Google Cloud skills for financial
services workflows for AI coding assistants (Gemini CLI, Claude Code,
Antigravity, Codex), following the Agent Plugins v1.0.0 layout
(`plugin.json` + `skills/<skill-name>/SKILL.md`):

1. **[`fsi-small-business-loan`](./skills/small-business-loan/)** — a small
   business loan processing agent: an ADK orchestrator with document
   extraction, underwriting, pricing and loan decision sub-agents, an
   **enforced human approval** gate, an LLM-as-judge quality gate, and
   **repair & resume** for incomplete applications. Quick start runs with
   in-memory state and bundled synthetic PDFs (Gemini on Vertex AI only); Full
   setup adds Firestore state, `gs://` applications and custom eligibility
   rules.

**Credits:** adapted from
[`contrib/python/small-business-loan-agent`](../../contrib/python/small-business-loan-agent/)
by the **AI FDE Financial Services** team — Nora Filali (POC, `norarfilali`),
contributor Neil Desai (`nmdesai`). · **License:** Apache-2.0

## Skills vs. the deployable sample

| Surface | Location | Persona |
|---|---|---|
| **Agent Plugin Skill** (this folder) | [`skills/small-business-loan`](./skills/small-business-loan/) | Developers using a coding assistant: Q-MODE interview → `design-spec.md` → `setup.py` → `adk web` in their own workspace |
| **Standalone sample** | [`contrib/python/small-business-loan-agent`](../../contrib/python/small-business-loan-agent/) | Platform engineers deploying with Agent Starter Pack / Agent Engine and Firestore |

## Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- `gcloud` with Application Default Credentials and Vertex AI enabled:
  ```bash
  gcloud auth application-default login
  export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
  ```

## Installation

```bash
npx skills add google/adk-recipes/plugins/fsi --skill fsi-small-business-loan
```

Or from a clone: `make -C plugins/fsi/skills/small-business-loan install-skill-local`.

## Run

Open your coding assistant in an empty workspace and ask it to "build a small
business loan agent"; it runs the Q-MODE setup interview and starts the agent
with `adk web`. A 3-minute demo script is in
[`skills/small-business-loan/DEMO.md`](./skills/small-business-loan/DEMO.md).
All sample documents are synthetic.
