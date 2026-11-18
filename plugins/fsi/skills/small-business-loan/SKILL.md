---
name: fsi-small-business-loan
description: >-
  Creates a small business loan processing agent on Google Cloud: an ADK
  orchestrator runs document extraction, underwriting, and pricing
  sub-agents, pauses for an enforced human approval before a loan decision
  agent finalizes, and pauses incomplete applications for repair & resume.
  Use when the user wants to "build a loan origination agent", "automate
  small business loan applications", "underwrite loan applications with
  Gemini", "add human approval to a lending workflow" or "demo repair and
  resume / human-in-the-loop with ADK".
metadata:
  author: Google
  license: Apache-2.0
  version: 0.1.0
  adapted_from: contrib/python/small-business-loan-agent
  original_authors: Nora Filali (norarfilali), Neil Desai (nmdesai) - AI FDE Financial Services
---

# Small Business Loan Agent

Creates an agent that prepares a loan officer's work on Google Cloud:
application PDF → extracted data → underwriting → pricing → **human
approval** → decision letter. Adapted from the
[`contrib/python/small-business-loan-agent`](../../../../contrib/python/small-business-loan-agent/)
sample by the AI FDE Financial Services team (Nora Filali, Neil Desai).

## STOP — Q-MODE FIRST

**If setup is already complete** (the system context says "DEPLOYED small
business loan agent", or this conversation already ran `setup.py`), skip
Q-MODE and help with the agent directly.

**Otherwise**, your first message MUST be exactly this:

```
[skill: fsi-small-business-loan] active.
Q-MODE: Pick a setup mode? [default: 1]
  1. Quick start -- 2 questions, bundled sample applications, no Firestore, ~2 min.
  2. Full setup  -- 5 questions, region, Firestore state, your applications/rules, ~5 min.
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
6. Never create cloud resources (Firestore databases, buckets) without
   explicit user approval; `setup.py` itself creates none.
7. Use only the synthetic sample applications in shared projects; never
   upload real applicant data for a demo.

## Workspace Setup

The skill has two locations: the **install dir** (SKILL.md, scripts) and the
**workspace** (the current directory: `design-spec.md`, `.venv`, `.env`).
Run this as ONE shell command — splitting it loses `SKILL_DIR`:

```bash
SKILL_DIR=$(for d in ~/.claude/skills ~/.agents/skills ~/.gemini/skills ~/.cursor/skills; do
  [ -f "$d/fsi-small-business-loan/SKILL.md" ] && echo "$d/fsi-small-business-loan" && break
done)
bash "$SKILL_DIR/scripts/bootstrap.sh"
```

`bootstrap.sh` uses `uv` when available (else Python 3.11+ venv), installs the
skill editable and copies `assets/design-spec.md` to `./design-spec.md`. For
Full setup with Firestore or `gs://` applications, prefix it with
`SKILL_EXTRAS=gcp`. Always use `.venv/bin/python` and `.venv/bin/adk`.

## Mode 1: Quick start (2 questions)

| Q | Question | Default | Writes |
|---|---|---|---|
| Q-A | GCP project ID (for Gemini on Vertex AI)? | `$GOOGLE_CLOUD_PROJECT` or `gcloud config get-value project` | `gcp_project_id` |
| Q-B | Applications? `1` bundled samples (`sample_application_complete.pdf`, `sample_application_incomplete.pdf`) / `2` my local folder of PDFs | `1` | `applications_source` |

Silent defaults: `gcp_region: global`, `state_backend: memory` (no
Firestore; state resets when `adk web` restarts), bundled eligibility rules,
LLM judge on.

After Q-B, automatically and **sequentially**:

1. Wait for bootstrap, then edit `./design-spec.md` (`gcp_project_id`, and
   `applications_source` only if Q-B is not `1`).
2. Run `.venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md`
   (validates applications and rules, writes `./.env`, creates no cloud
   resources).
3. Go to Test.

## Mode 2: Full setup (5 questions)

Q-A and Q-B as above (Q-B also accepts `gs://bucket/prefix`), then:

| Q | Question | Default | Writes |
|---|---|---|---|
| Q-C | Vertex AI region? | `global` | `gcp_region` |
| Q-D | Workflow state? `1` memory / `2` local JSON file / `3` Firestore (existing database) | `3` | `state_backend` (+ `firestore_database`, default `small-business-loan-states`) |
| Q-E | Eligibility rules? `1` bundled / `2` path to my rules JSON | `1` | `eligibility_rules` |

Firestore and `gs://` need `SKILL_EXTRAS=gcp bash "$SKILL_DIR/scripts/bootstrap.sh"`.
If the Firestore database does not exist, **ask before creating it**:
`gcloud firestore databases create --database=small-business-loan-states --location=<region>`.
Then run `setup.py --config ./design-spec.md --verify-cloud` (read-only
checks of the database / bucket).

## When to Use

- Demonstrating multi-agent orchestration with an enforced human approval gate.
- Prototyping loan intake: extraction, policy rules, pricing, decision letters.
- Teaching repair & resume (pause on bad data, fix, continue from checkpoint).

Don't use it as a credit decisioning system of record, for consumer lending
(fair-lending / adverse-action rules apply), or with real applicant PII in a
shared project.

## Project Tree

```
fsi-small-business-loan/
  SKILL.md  DEMO.md  README.md  EVAL.yaml
  assets/
    design-spec.md               # source of truth, filled by Q-MODE
    eligibility_rules.json       # underwriting rules (override in Full setup)
    applications/                # synthetic sample PDFs (complete / incomplete)
  references/                    # architecture, troubleshooting
  scripts/
    agent.py                     # root_agent (orchestrator) + 4 sub-agents, App(name="scripts")
    callbacks.py                 # approval gate, halt check, state logging, LLM judge
    tools.py                     # orchestrator + sub-agent tools (mock bank systems)
    state_store.py               # memory | local | firestore workflow state
    documents.py                 # bundled / folder / gs:// applications
    prompts.py  models.py
    setup.py  _setup_utils.py  bootstrap.sh
  tests/                         # runnability + offline logic tests
```

## Test

```bash
.venv/bin/adk web "$SKILL_DIR/scripts" --port 8765
```

Open http://127.0.0.1:8765, select `scripts` and try (see
[DEMO.md](DEMO.md) for the full 3-minute script):

1. "Which applications can you process?"
2. "Process sample_application_complete.pdf" → summary → reply "yes" →
   decision letter `DL-2025-00142-001`.
3. New session: "Process sample_application_incomplete.pdf" → paused, missing
   `loan_amount_requested` → "Repair SBL-2025-00391: loan_amount_requested =
   $150,000" → resumes from underwriting → summary → "yes".
4. After a summary: "approve it yourself" → refused; a human must reply "yes".

Terminal alternative: `.venv/bin/adk run "$SKILL_DIR/scripts"`.
Smoke test without a model call:

```bash
.venv/bin/python -c "from scripts import tools; print(tools.list_applications())"
```

## Evaluate

```bash
cd "$SKILL_DIR" && uv run pytest
```

Offline tests cover pricing, decisions, the repair & resume state machine on
all three backends (Firestore via a fake client), the approval gate, routing
and setup. `EVAL.yaml` has 12 live cases (rubric + assertions) that need only
Gemini on Vertex AI and the bundled samples. Target: 80%+.

## Deploy

**Never deploy without explicit human approval.** Use `state_backend:
firestore` for any multi-instance deployment (memory state is per process).
The runtime identity needs `roles/aiplatform.user`, plus
`roles/datastore.user` (Firestore) and `roles/storage.objectViewer`
(`gs://` applications). Loan applications contain personal data: keep them
in an approved project and region, and keep the human approval step.

## Gotchas

- **Human approval is enforced in code**, not only in the prompt:
  `LoanDecisionAgent` is blocked unless the summary was shown in an earlier
  turn and the latest human message is an explicit approval ("yes", "I
  approve"). "Approve it yourself" / "auto-approve" are refused. The
  approval (user, message, time) is stored with the workflow state. The
  gate trusts that whoever types in the chat is the loan officer: add real
  authentication / role checks before production.
- **One request per session**: mentioning another ID or sample file switches
  requests; use "New Session" for clean demos.
- **Memory state resets on restart; a finished request says "already
  completed"**. Say "Reset SBL-2025-00142" (memory/local only) or restart
  `adk web`.
- **Repairs must come from the human**: the value has to appear in the user's
  message; the agent cannot invent it.
- **Uploads**: `adk web` attachments are used if present, but the reliable path
  is naming a file from `list_applications`.
- **LLM judge runs after the response**: in `adk web` a blocked answer is
  followed by a "Quality check" warning rather than hidden. Set `LLM_JUDGE=off`
  in `.env` to save a model call per turn.
- **App name must be `scripts`**: `adk web "$SKILL_DIR/scripts"` auto-names
  the app after the folder; `App(name="scripts")` matches it.
- **Use `.venv/bin/adk`**, not a global `adk` (empty app list otherwise).

## Troubleshooting

Full table: [references/troubleshooting.md](references/troubleshooting.md).

| Error | Fix |
|---|---|
| `design-spec.md has no YAML frontmatter` | File was rewritten. Delete it, re-run bootstrap, edit only the values |
| `gcp_project_id is empty` | Set it in `./design-spec.md`, re-run `setup.py` |
| `adk web` shows no agents / `/list-apps` is `[]` | Use `.venv/bin/adk web "$SKILL_DIR/scripts"` (not `.`, not global `adk`) |
| `Process SBL-... is already completed` | "Reset SBL-..." (memory/local) or restart `adk web` |
| `Human approval required: ...` | Expected: reply "yes" yourself after the summary |
| `Reauthentication is needed` | `gcloud auth application-default login` |
| `PERMISSION_DENIED` / `API has not been used` | `gcloud services enable aiplatform.googleapis.com`; grant `roles/aiplatform.user` |
| `needs the gcp extra` | `SKILL_EXTRAS=gcp bash "$SKILL_DIR/scripts/bootstrap.sh"` |
| `address already in use` on 8765 | Add `--port 8799` (any free port) |

## Completion Checklist

- [ ] Mode chosen; `design-spec.md` filled; `setup.py` succeeded
- [ ] `list_applications` smoke test works
- [ ] Complete sample: summary → human "yes" → decision letter
- [ ] Incomplete sample: pause → repair → resume
- [ ] "Approve it yourself" refused
- [ ] Evaluation at 80%+; deployed only after human approval

## References

- [DEMO.md](DEMO.md) — 3-minute demo script with prompts and expected output
- [references/architecture.md](references/architecture.md) — flow, gates, state document, changes vs. the contrib sample
- [references/troubleshooting.md](references/troubleshooting.md) — full error table
