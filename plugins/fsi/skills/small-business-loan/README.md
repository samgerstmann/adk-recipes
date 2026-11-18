# fsi-small-business-loan

Agent Plugins skill that builds a **small business loan processing agent**
with the Agent Development Kit (ADK) and Gemini on Vertex AI:

- **Orchestrator + 4 sub-agents**: document extraction (multimodal PDF),
  underwriting (internal records + eligibility rules), pricing (risk tier,
  amortized payment), loan decision (decision letter).
- **Enforced human approval**: the decision agent is blocked in code until a
  human explicitly approves the summary in a later turn; "approve it yourself"
  is refused; the approval is recorded.
- **Repair & resume**: incomplete applications pause; a human supplies the
  missing value (in chat or by editing the state document) and the workflow
  resumes from the checkpoint.
- **LLM-as-judge** after-agent callback for trajectory and grounding checks.
- **Pluggable state**: `memory` (Quick start, no cloud resources), `local`
  JSON file, or `firestore`.

See [SKILL.md](SKILL.md) for the Q-MODE installer, [DEMO.md](DEMO.md) for a
3-minute demo and [references/architecture.md](references/architecture.md)
for the design and the changes vs. the original sample.

## Credits

Adapted from
[`contrib/python/small-business-loan-agent`](../../../../contrib/python/small-business-loan-agent/)
by the **AI FDE Financial Services** team: Nora Filali (POC) and Neil Desai
(contributor). The original sample is unchanged and remains the reference for
Agent Starter Pack / Agent Engine deployment. All sample data is fictional.

## Quick start (without a coding assistant)

```bash
SKILL_DIR=$(pwd)/plugins/fsi/skills/small-business-loan   # from a repo clone
mkdir -p ~/loan-demo && cd ~/loan-demo
bash "$SKILL_DIR/scripts/bootstrap.sh"
sed -i "s/^gcp_project_id: \"\"/gcp_project_id: $(gcloud config get-value project)/" design-spec.md
.venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md
.venv/bin/adk web "$SKILL_DIR/scripts" --port 8765
```

## Tests

```bash
cd "$SKILL_DIR" && uv run pytest     # offline, no cloud calls
```

License: Apache-2.0.
