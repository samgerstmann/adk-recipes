# Troubleshooting

| Symptom / error | Cause | Fix |
|---|---|---|
| `design-spec.md has no YAML frontmatter` | The file was rewritten as plain Markdown | Delete it, re-run `bootstrap.sh`, edit only the values between the `---` lines |
| `gcp_project_id is empty` | Q-A answer not written | Edit `gcp_project_id`, re-run `setup.py` |
| `Pack <x>: ... not found` | Unknown pack name / path | Use `small-business-loan`, `trade-finance`, or a folder with `schema.json` |
| `Pack ...: bad field` / `unknown type` / `references unknown field` | Invalid `schema.json` / `rules.yaml` | Field types: string, money, number, date; rule types: required, compare, date_before, equals; refs `<doc_type>.<field>` |
| `Pack ... has no samples/` | Custom pack without samples | Set `documents_source` to your folder |
| `needs the gcp extra` | Firestore / GCS / BigQuery client missing | `SKILL_EXTRAS=gcp bash "$SKILL_DIR/scripts/bootstrap.sh"` |
| `adk web` loads but no agents / `/list-apps` is `[]` | Global `adk` or wrong folder | `.venv/bin/adk web "$SKILL_DIR/scripts" --port 8767` |
| `Session not found ... app name` | App name mismatch | Keep `App(name="scripts")`, point `adk web` at the `scripts` folder |
| `Reauthentication is needed` | ADC expired | `gcloud auth application-default login` |
| `PERMISSION_DENIED` / `API has not been used` | Vertex AI not enabled / no role | `gcloud services enable aiplatform.googleapis.com`; grant `roles/aiplatform.user` |
| `404 ... model` | Model not available in the location | Set `MODEL_NAME` / `EXTRACTION_MODEL_NAME` in `.env`, or use `global` |
| `split_problems: pages not assigned` / `overlapping pages` | Split did not partition the package | "reprocess <file>"; check the PDF; adjust doc type descriptions in `schema.json` |
| Many fields in the review lane | Quotes not found on the cited page (caps confidence at 0.6) or thresholds too strict | Check `quote` / `page`; calibrate `TAU_HIGH` / `TAU_LOW` |
| `Correction rejected: the value must be typed by the human reviewer` | The model tried a value not in your message | Type the value: `correct <field> = <value>` |
| `Accept rejected` | No explicit accept/confirm in the message | "accept the remaining fields" |
| `Cannot approve: N field(s) still need review` | Review queue not empty | Correct or accept them first |
| `Human approval required: ... later message` | Approval in the same turn as a change | Look at the final record, then send "Approve" |
| `Reviewer note required` | Checks failed | `Approve. Note: <reason>` |
| `NotImplementedError: EXTRACTOR=documentai` | Document AI is an opt-in stub | Implement `extraction.extract_fields` (references/document-ai.md) or set `extractor: gemini` |
| `BQ_TABLE must be project.dataset.table` / BigQuery insert errors | Table missing or schema mismatch | Create the table (ask first) with columns package_id, pack, approved_at, approved_by, record (STRING) |
| Package already processed / approved | Saved state | "reset" (memory/local), restart `adk web` (memory), or delete `.doc_intake_state.json` (local) |
| `address already in use` | Port taken | `--port 8799` |
