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

"""Loan application documents: bundled samples, a local folder, or GCS.

`adk web` uploads do not always reach sub-agents, so applications are
addressed by file name and loaded here. APPLICATIONS_SOURCE selects the
source: empty (bundled samples), a local folder, or `gs://bucket[/prefix]`.
"""

import json
import os
import re
import zlib
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
BUNDLED_DIR = ASSETS / "applications"

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Demo request IDs for the bundled samples (the PDFs carry no request ID).
SAMPLE_REQUEST_IDS = {
    "sample_application_complete.pdf": "SBL-2025-00142",
    "sample_application_incomplete.pdf": "SBL-2025-00391",
}

REQUEST_ID_RE = re.compile(r"SBL-\d{4}-\d{5}")
FILENAME_RE = re.compile(r"[\w.-]+\.(?:pdf|png|jpe?g)", re.I)


def source() -> str:
    return (os.getenv("APPLICATIONS_SOURCE") or "").strip() or str(BUNDLED_DIR)


def _split_gcs(uri: str) -> tuple[str, str]:
    bucket, _, prefix = uri[len("gs://") :].partition("/")
    return bucket, prefix.strip("/")


def list_application_files() -> list[str]:
    """File names of the available applications."""
    src = source()
    if src.startswith("gs://"):
        from google.cloud import storage  # optional dependency

        bucket, prefix = _split_gcs(src)
        blobs = storage.Client().list_blobs(bucket, prefix=prefix or None)
        names = [b.name.rsplit("/", 1)[-1] for b in blobs]
    else:
        folder = Path(src)
        names = [p.name for p in folder.iterdir()] if folder.is_dir() else []
    return sorted(n for n in names if Path(n).suffix.lower() in MIME_TYPES)


def request_id_for(filename: str) -> str:
    """Demo request ID for a file: fixed for samples, stable hash otherwise."""
    if filename in SAMPLE_REQUEST_IDS:
        return SAMPLE_REQUEST_IDS[filename]
    return f"SBL-2026-{zlib.crc32(filename.encode()) % 100000:05d}"


def filename_for(request_id: str) -> str | None:
    """Inverse of request_id_for over the available files."""
    for name in list_application_files():
        if request_id_for(name) == request_id:
            return name
    return None


def load_application(filename: str) -> tuple[bytes, str]:
    """Return (bytes, mime type) of an application by file name."""
    name = Path(filename).name  # no path traversal
    mime = MIME_TYPES.get(Path(name).suffix.lower())
    if not mime:
        raise ValueError(f"Unsupported file type: {name}")
    src = source()
    if src.startswith("gs://"):
        from google.cloud import storage

        bucket, prefix = _split_gcs(src)
        blob = storage.Client().bucket(bucket).blob(f"{prefix}/{name}" if prefix else name)
        return blob.download_as_bytes(), mime
    path = Path(src) / name
    if not path.is_file():
        raise FileNotFoundError(f"Application {name} not found in {src}")
    return path.read_bytes(), mime


def extract_request_id(text: str) -> str | None:
    match = REQUEST_ID_RE.search(text or "")
    return match.group(0) if match else None


def extract_filename(text: str) -> str | None:
    match = FILENAME_RE.search(text or "")
    return match.group(0) if match else None


def eligibility_rules() -> dict:
    path = (os.getenv("ELIGIBILITY_RULES_PATH") or "").strip()
    rules_path = Path(path) if path else ASSETS / "eligibility_rules.json"
    return json.loads(rules_path.read_text(encoding="utf-8"))
