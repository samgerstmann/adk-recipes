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

"""Domain packs and document sources.

A pack is a folder `assets/packs/<name>/` (or any folder via PACK_PATH):

    schema.json   doc types and the fields to extract per type
    rules.yaml    declarative consistency checks (see rules.py)
    samples/      example document packages (PDF / PNG / JPG)

Adding a pack needs no code changes: create the folder and set PACK.
"""

import json
import os
from pathlib import Path

import yaml

ASSETS = Path(__file__).resolve().parent.parent / "assets"
PACKS_DIR = ASSETS / "packs"
DEFAULT_PACK = "small-business-loan"
FIELD_TYPES = {"string", "money", "number", "date"}
RULE_TYPES = {"required", "compare", "date_before", "equals"}
MIME_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class PackError(ValueError):
    """The pack folder is missing files or is inconsistent."""


def pack_dir() -> Path:
    path = (os.getenv("PACK_PATH") or "").strip()
    if path:
        return Path(path).expanduser().resolve()
    return PACKS_DIR / ((os.getenv("PACK") or DEFAULT_PACK).strip())


def list_packs() -> list[str]:
    return sorted(p.name for p in PACKS_DIR.iterdir() if (p / "schema.json").exists())


def _refs(rule: dict) -> list[str]:
    return [rule[k] for k in ("left", "right", "tolerance_pct_from") if isinstance(rule.get(k), str)]


def load_pack(path: str | Path | None = None) -> dict:
    """Load and validate schema.json + rules.yaml of a pack."""
    folder = Path(path) if path else pack_dir()
    schema_path, rules_path = folder / "schema.json", folder / "rules.yaml"
    if not schema_path.exists():
        raise PackError(f"{schema_path} not found (packs: {list_packs()})")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    doc_types = schema.get("doc_types") or {}
    if not doc_types:
        raise PackError(f"{schema_path}: 'doc_types' is empty")
    for name, spec in doc_types.items():
        fields = spec.get("fields") or []
        if not fields:
            raise PackError(f"{schema_path}: doc type {name} has no fields")
        for field in fields:
            if not field.get("name") or field.get("type", "string") not in FIELD_TYPES:
                raise PackError(f"{schema_path}: bad field in {name}: {field} (types: {sorted(FIELD_TYPES)})")
    rules = (yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}).get("rules", []) if rules_path.exists() else []
    seen = set()
    for rule in rules:
        if rule.get("type") not in RULE_TYPES:
            raise PackError(f"{rules_path}: rule {rule.get('id')} has unknown type {rule.get('type')} ({sorted(RULE_TYPES)})")
        if not rule.get("id") or rule["id"] in seen:
            raise PackError(f"{rules_path}: rule ids must be unique and non-empty ({rule.get('id')})")
        seen.add(rule["id"])
        for ref in _refs(rule):
            doc_type, _, field = ref.partition(".")
            names = {f["name"] for f in doc_types.get(doc_type, {}).get("fields", [])}
            if field not in names:
                raise PackError(f"{rules_path}: rule {rule['id']} references unknown field {ref}")
    return {
        "name": schema.get("pack") or folder.name,
        "description": schema.get("description", ""),
        "doc_types": doc_types,
        "rules": rules,
        "dir": str(folder),
    }


def documents_source() -> str:
    return (os.getenv("DOCUMENTS_SOURCE") or "").strip() or str(pack_dir() / "samples")


def _split_gcs(uri: str) -> tuple[str, str]:
    bucket, _, prefix = uri[len("gs://") :].partition("/")
    return bucket, prefix.strip("/")


def list_document_files() -> list[str]:
    src = documents_source()
    if src.startswith("gs://"):
        from google.cloud import storage  # optional dependency

        bucket, prefix = _split_gcs(src)
        names = [b.name.rsplit("/", 1)[-1] for b in storage.Client().list_blobs(bucket, prefix=prefix or None)]
    else:
        folder = Path(src)
        names = [p.name for p in folder.iterdir()] if folder.is_dir() else []
    return sorted(n for n in names if Path(n).suffix.lower() in MIME_TYPES)


def load_document(filename: str) -> tuple[bytes, str]:
    name = Path(filename).name  # no path traversal
    mime = MIME_TYPES.get(Path(name).suffix.lower())
    if not mime:
        raise ValueError(f"Unsupported file type: {name}")
    src = documents_source()
    if src.startswith("gs://"):
        from google.cloud import storage

        bucket, prefix = _split_gcs(src)
        blob = storage.Client().bucket(bucket).blob(f"{prefix}/{name}" if prefix else name)
        return blob.download_as_bytes(), mime
    path = Path(src) / name
    if not path.is_file():
        raise FileNotFoundError(f"{name} not found in {src}")
    return path.read_bytes(), mime
