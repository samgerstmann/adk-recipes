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

"""Validate ./design-spec.md (pack, documents, options) and write ./.env.

Creates NO cloud resources. Quick start only needs Gemini on Vertex AI.
Full setup can point at an existing Firestore database, GCS bucket or
BigQuery table, checked read-only with --verify-cloud.

Usage: .venv/bin/python "$SKILL_DIR/scripts/setup.py" --config ./design-spec.md
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _setup_utils import SpecError, example_defaults, load_config, merge_env  # noqa: E402
from packs import MIME_TYPES, PACKS_DIR, PackError, load_pack  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
BACKENDS = ("memory", "local", "firestore")
OUTPUTS = ("local", "bigquery", "gcs")


def _bundled(value: object) -> bool:
    return str(value or "bundled").strip().lower() in ("bundled", "default", "")


def _need_gcp(what: str, module: str) -> None:
    if importlib.util.find_spec(module) is None:
        raise SpecError(f'{what} needs the gcp extra: SKILL_EXTRAS=gcp bash "$SKILL_DIR/scripts/bootstrap.sh"')


def build_env(spec: dict) -> tuple[dict, list[str]]:
    """Validate the spec; return (.env values, report lines). Raises SpecError."""
    report: list[str] = []
    project = str(spec.get("gcp_project_id") or "").strip()
    if not project:
        raise SpecError("gcp_project_id is empty: set it in design-spec.md.")

    pack_value = str(spec.get("pack") or "small-business-loan").strip()
    pack_path = PACKS_DIR / pack_value if (PACKS_DIR / pack_value).is_dir() else Path(pack_value).expanduser().resolve()
    try:
        pack = load_pack(pack_path)
    except (PackError, OSError, ValueError) as e:
        raise SpecError(f"Pack {pack_value}: {e}") from e
    report.append(f"Pack: {pack['name']} ({len(pack['doc_types'])} doc types, {len(pack['rules'])} rules)")

    source = spec.get("documents_source")
    if _bundled(source):
        source_value = ""
        folder = pack_path / "samples"
        files = sorted(p.name for p in folder.iterdir() if p.suffix.lower() in MIME_TYPES) if folder.is_dir() else []
        if not files:
            raise SpecError(f"Pack {pack['name']} has no samples/: set documents_source")
        report.append(f"Documents: {len(files)} bundled samples ({', '.join(files)})")
    elif str(source).startswith("gs://"):
        _need_gcp("gs:// documents", "google.cloud.storage")
        source_value = str(source).rstrip("/")
        report.append(f"Documents: {source_value} (read at runtime)")
    else:
        folder = Path(str(source)).expanduser().resolve()
        files = sorted(p.name for p in folder.iterdir() if p.suffix.lower() in MIME_TYPES) if folder.is_dir() else []
        if not files:
            raise SpecError(f"No .pdf/.png/.jpg packages in {folder}")
        source_value = str(folder)
        report.append(f"Documents: {len(files)} in {folder}")

    tau_high, tau_low = float(spec.get("tau_high", 0.9)), float(spec.get("tau_low", 0.5))
    if not 0 <= tau_low <= tau_high <= 1:
        raise SpecError("thresholds must satisfy 0 <= tau_low <= tau_high <= 1")

    backend = str(spec.get("state_backend") or "memory").strip().lower()
    if backend not in BACKENDS:
        raise SpecError(f"state_backend must be one of {BACKENDS}, got {backend!r}")
    if backend == "firestore":
        _need_gcp("state_backend firestore", "google.cloud.firestore")

    destination = str(spec.get("output_destination") or "local").strip().lower()
    if destination not in OUTPUTS:
        raise SpecError(f"output_destination must be one of {OUTPUTS}, got {destination!r}")
    if destination == "bigquery":
        _need_gcp("BigQuery output", "google.cloud.bigquery")
        if str(spec.get("bq_table") or "").count(".") != 2:
            raise SpecError("bq_table must be project.dataset.table (existing table)")
    if destination == "gcs" and not str(spec.get("output_gcs_uri") or "").startswith("gs://"):
        raise SpecError("output_gcs_uri must be gs://bucket[/prefix]")

    extractor = str(spec.get("extractor") or "gemini").strip().lower()
    if extractor not in ("gemini", "documentai"):
        raise SpecError("extractor must be gemini or documentai")
    if extractor == "documentai":
        report.append("WARNING: extractor documentai is an opt-in stub - implement extraction.extract_fields first")

    env = {
        "GOOGLE_CLOUD_PROJECT": project,
        "GOOGLE_CLOUD_LOCATION": str(spec.get("gcp_region") or "global").strip(),
        "GOOGLE_GENAI_USE_VERTEXAI": "True",
        "PACK": pack_value if (PACKS_DIR / pack_value).is_dir() else "",
        "PACK_PATH": "" if (PACKS_DIR / pack_value).is_dir() else str(pack_path),
        "DOCUMENTS_SOURCE": source_value,
        "TAU_HIGH": tau_high,
        "TAU_LOW": tau_low,
        "STATE_BACKEND": backend,
        "OUTPUT_DESTINATION": destination,
        "EXTRACTOR": extractor,
    }
    if backend == "firestore":
        env["FIRESTORE_DATABASE"] = str(spec.get("firestore_database") or "(default)")
    if destination == "bigquery":
        env["BQ_TABLE"] = str(spec["bq_table"])
    if destination == "gcs":
        env["OUTPUT_GCS_URI"] = str(spec["output_gcs_uri"])
    if spec.get("model_name"):
        env["MODEL_NAME"] = str(spec["model_name"]).strip()
    report.append(f"Lanes: auto >= {tau_high}, review >= {tau_low}; state: {backend}; output: {destination}")
    return env, report


def verify_cloud(env: dict) -> list[str]:
    """Read-only checks of existing Firestore / GCS / BigQuery resources."""
    out = []
    if env["STATE_BACKEND"] == "firestore":
        from google.cloud import firestore

        client = firestore.Client(project=env["GOOGLE_CLOUD_PROJECT"], database=env["FIRESTORE_DATABASE"])
        list(client.collection("doc_intake_packages").limit(1).stream())
        out.append(f"Firestore {env['FIRESTORE_DATABASE']}: reachable")
    if env["DOCUMENTS_SOURCE"].startswith("gs://"):
        from google.cloud import storage

        bucket, _, prefix = env["DOCUMENTS_SOURCE"][5:].partition("/")
        n = len(list(storage.Client(project=env["GOOGLE_CLOUD_PROJECT"]).list_blobs(bucket, prefix=prefix or None, max_results=50)))
        out.append(f"GCS {env['DOCUMENTS_SOURCE']}: {n} objects")
    if env["OUTPUT_DESTINATION"] == "bigquery":
        from google.cloud import bigquery

        table = bigquery.Client(project=env["GOOGLE_CLOUD_PROJECT"]).get_table(env["BQ_TABLE"])
        out.append(f"BigQuery {env['BQ_TABLE']}: {len(table.schema)} columns")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="design-spec.md")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--verify-cloud", action="store_true", help="read-only check of Firestore/GCS/BigQuery")
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
    print("No cloud resources created.")
    print(f"Wrote {env_path.resolve()} ({', '.join(env)})")
    if os.environ.get("PACK") and os.environ["PACK"] != env["PACK"]:
        print("Note: shell PACK differs from design-spec; the shell value wins at runtime.")


if __name__ == "__main__":
    main()
