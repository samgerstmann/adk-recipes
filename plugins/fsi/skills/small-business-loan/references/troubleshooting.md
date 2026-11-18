# Troubleshooting

| Symptom / error | Cause | Fix |
|---|---|---|
| `design-spec.md has no YAML frontmatter` | The file was rewritten as plain Markdown | Delete it, re-run `bootstrap.sh`, edit only the values between the `---` lines |
| `design-spec.md not found` | Bootstrap not run in this workspace | Run the Workspace Setup command from SKILL.md |
| `gcp_project_id is empty` | Q-A answer not written | Edit `gcp_project_id`, re-run `setup.py` |
| `No .pdf/.png/.jpg applications in <dir>` | Wrong `applications_source` | Point it at a folder with application files, or `bundled` |
| `state_backend firestore needs the gcp extra` | Firestore client not installed | `SKILL_EXTRAS=gcp bash "$SKILL_DIR/scripts/bootstrap.sh"` |
| `setup.py --verify-cloud` fails with `NotFound` / `404 database` | Firestore database does not exist | Ask the user, then `gcloud firestore databases create --database=small-business-loan-states --location=<region>` |
| `adk web` loads but shows no agents, `/list-apps` returns `[]` | Global `adk` or wrong folder | `.venv/bin/adk web "$SKILL_DIR/scripts" --port 8765` |
| `Session not found ... app name` | App name mismatch | Keep `App(name="scripts")` and point `adk web` at the `scripts` folder |
| `Reauthentication is needed` | ADC expired | `gcloud auth application-default login` |
| `PERMISSION_DENIED` / `API has not been used` | Vertex AI not enabled / no role | `gcloud services enable aiplatform.googleapis.com`; grant `roles/aiplatform.user` |
| `404 ... model ... not found` | Model not available at `GOOGLE_CLOUD_LOCATION` | Set `MODEL_NAME` in `.env` to an available Gemini model, or use `global` |
| `response_schema ... enum ... cannot be empty` | Old schema with empty enum values | Use the bundled `models.py` (plain-string `loan_purpose`) |
| Extraction returns empty fields | No document resolved | Name a file from `list_applications` ("Process sample_application_complete.pdf") |
| `Process SBL-... is already completed` | Memory/local state kept the finished run | "Reset SBL-..." or restart `adk web` (memory) / delete `.loan_state.json` (local) |
| `Cannot proceed to UnderwritingAgent: Pending approval - Missing ...` | Expected pause for the incomplete sample | "Repair SBL-2025-00391: loan_amount_requested = $150,000" |
| `Repair rejected: the value must be typed by the human operator` | The model tried to supply a value the human did not type | Type the value in your message |
| `Human approval required: ...` | Expected: the approval gate | Reply "yes" (or "I approve") after the summary, as the human |
| "Quality check: the response above could not be verified" | LLM judge flagged the answer | Retry; check the `_llm_judge_audit` session state in `adk web`; `LLM_JUDGE=off` to disable |
| Agent keeps saying a tool is broken after a fix | Session memory | Click "New Session" in `adk web` |
| `address already in use` | Port taken | `--port 8799` |
| A sub-agent seems stuck / slow | Model turn loop or slow model | Set `LOAN_AGENT_TRACE=1` in `.env` and watch the `adk web` console for `TRACE <Agent>` lines |
