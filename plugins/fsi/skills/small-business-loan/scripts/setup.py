# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Validate ./design-spec.md and write ./.env for the loan agent.

Creates NO cloud resources. Quick start only needs Gemini on Vertex AI;
Full setup can point at an existing Firestore database and GCS bucket,
which this script checks (read-only) when --verify-cloud is passed.

Usage: .venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _setup_utils import SpecError, example_defaults, load_config, merge_env  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS = SKILL_DIR / "assets"
BACKENDS = ("memory", "local", "firestore")
EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")


def _bundled(value: object) -> bool:
    return str(value or "bundled").strip().lower() in ("bundled", "default", "")


def build_env(spec: dict) -> tuple[dict, list[str]]:
    """Validate the spec; return (.env values, report lines). Raises SpecError."""
    report: list[str] = []
    project = str(spec.get("gcp_project_id") or "").strip()
    if not project:
        raise SpecError("gcp_project_id is empty: set it in design-spec.md.")
    backend = str(spec.get("state_backend") or "memory").strip().lower()
    if backend not in BACKENDS:
        raise SpecError(f"state_backend must be one of {BACKENDS}, got {backend!r}")

    source = spec.get("applications_source")
    if _bundled(source):
        source_value = ""
        files = sorted(p.name for p in (ASSETS / "applications").iterdir() if p.suffix in EXTENSIONS)
        report.append(f"Applications: {len(files)} bundled samples ({', '.join(files)})")
    elif str(source).startswith("gs://"):
        source_value = str(source).rstrip("/")
        if importlib.util.find_spec("google.cloud.storage") is None:
            raise SpecError('gs:// source needs the gcp extra: uv pip install -e "$SKILL_DIR[gcp]"')
        report.append(f"Applications: {source_value} (read at runtime)")
    else:
        folder = Path(str(source)).expanduser().resolve()
        files = sorted(p.name for p in folder.iterdir() if p.suffix.lower() in EXTENSIONS) if folder.is_dir() else []
        if not files:
            raise SpecError(f"No .pdf/.png/.jpg applications in {folder}")
        source_value = str(folder)
        report.append(f"Applications: {len(files)} in {folder}")

    rules = spec.get("eligibility_rules")
    rules_path = ASSETS / "eligibility_rules.json" if _bundled(rules) else Path(str(rules)).expanduser().resolve()
    try:
        parsed = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SpecError(f"Cannot read eligibility rules {rules_path}: {e}") from e
    if not isinstance(parsed.get("rules"), list) or not parsed["rules"]:
        raise SpecError(f"{rules_path} must contain a non-empty 'rules' list")
    report.append(f"Eligibility rules: {len(parsed['rules'])} from {rules_path.name}")

    if backend == "firestore" and importlib.util.find_spec("google.cloud.firestore") is None:
        raise SpecError('state_backend firestore needs the gcp extra: uv pip install -e "$SKILL_DIR[gcp]"')
    report.append(f"State backend: {backend}")

    env = {
        "GOOGLE_CLOUD_PROJECT": project,
        "GOOGLE_CLOUD_LOCATION": str(spec.get("gcp_region") or "global").strip(),
        "GOOGLE_GENAI_USE_VERTEXAI": "True",
        "STATE_BACKEND": backend,
        "APPLICATIONS_SOURCE": source_value,
        "ELIGIBILITY_RULES_PATH": "" if _bundled(rules) else str(rules_path),
    }
    if backend == "firestore":
        env["FIRESTORE_DATABASE"] = str(spec.get("firestore_database") or "small-business-loan-states")
    if spec.get("model_name"):
        env["MODEL_NAME"] = str(spec["model_name"]).strip()
    return env, report


def verify_cloud(env: dict) -> list[str]:
    """Read-only checks of existing Firestore / GCS resources."""
    out = []
    if env["STATE_BACKEND"] == "firestore":
        from google.cloud import firestore

        client = firestore.Client(project=env["GOOGLE_CLOUD_PROJECT"], database=env["FIRESTORE_DATABASE"])
        list(client.collection("process_states").limit(1).stream())
        out.append(f"Firestore database {env['FIRESTORE_DATABASE']}: reachable")
    if env["APPLICATIONS_SOURCE"].startswith("gs://"):
        from google.cloud import storage

        bucket, _, prefix = env["APPLICATIONS_SOURCE"][5:].partition("/")
        names = [b.name for b in storage.Client(project=env["GOOGLE_CLOUD_PROJECT"]).list_blobs(bucket, prefix=prefix or None, max_results=50)]
        out.append(f"GCS {env['APPLICATIONS_SOURCE']}: {len(names)} objects")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="design-spec.md")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--verify-cloud", action="store_true", help="read-only check of Firestore/GCS")
    args = parser.parse_args()
    try:
        env, report = build_env(load_config(args.config))
        if args.verify_cloud:
            report += verify_cloud(env)
    except SpecError as e:
        sys.exit(f"ERROR: {e}")
    env_path = Path(args.env_file)
    merge_env(env_path, env, example_defaults(SKILL_DIR / ".env.example"))
    for line in report:
        print(line)
    print("No cloud resources created (Gemini on Vertex AI only" + (", existing Firestore" if env["STATE_BACKEND"] == "firestore" else "") + ").")
    print(f"Wrote {env_path.resolve()} ({', '.join(env)})")
    if os.environ.get("GOOGLE_CLOUD_PROJECT") not in (None, env["GOOGLE_CLOUD_PROJECT"]):
        print("Note: shell GOOGLE_CLOUD_PROJECT differs from design-spec; the shell value wins at runtime.")


if __name__ == "__main__":
    main()
