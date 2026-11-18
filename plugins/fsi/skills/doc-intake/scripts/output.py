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

"""Write the approved record: local JSON (default), BigQuery or GCS.

OUTPUT_DESTINATION = local | bigquery | gcs
  local    -> OUTPUT_DIR/<package_id>.json (default ./records)
  bigquery -> BQ_TABLE (project.dataset.table) row: package_id, pack,
              approved_at, approved_by, record (JSON string). Table must exist.
  gcs      -> OUTPUT_GCS_URI (gs://bucket/prefix)/<package_id>.json
"""

import json
import os
from pathlib import Path


def write_record(record: dict, bq_client=None, gcs_client=None) -> dict:
    destination = (os.getenv("OUTPUT_DESTINATION") or "local").strip().lower()
    package_id = record["package_id"]
    payload = json.dumps(record, indent=2, ensure_ascii=False)
    if destination == "local":
        out_dir = Path(os.getenv("OUTPUT_DIR") or "records")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{package_id}.json"
        path.write_text(payload, encoding="utf-8")
        return {"destination": "local", "location": str(path.resolve())}
    if destination == "bigquery":
        table = os.getenv("BQ_TABLE") or ""
        if table.count(".") != 2:
            raise ValueError("BQ_TABLE must be project.dataset.table")
        if bq_client is None:
            from google.cloud import bigquery  # optional dependency

            bq_client = bigquery.Client(project=table.split(".")[0])
        row = {
            "package_id": package_id,
            "pack": record["pack"],
            "approved_at": record["approval"]["approved_at"],
            "approved_by": record["approval"]["approved_by"],
            "record": payload,
        }
        errors = bq_client.insert_rows_json(table, [row])
        if errors:
            raise RuntimeError(f"BigQuery insert failed: {errors}")
        return {"destination": "bigquery", "location": table}
    if destination == "gcs":
        uri = (os.getenv("OUTPUT_GCS_URI") or "").rstrip("/")
        if not uri.startswith("gs://"):
            raise ValueError("OUTPUT_GCS_URI must be gs://bucket[/prefix]")
        bucket, _, prefix = uri[5:].partition("/")
        if gcs_client is None:
            from google.cloud import storage

            gcs_client = storage.Client()
        name = f"{prefix}/{package_id}.json" if prefix else f"{package_id}.json"
        gcs_client.bucket(bucket).blob(name).upload_from_string(payload, content_type="application/json")
        return {"destination": "gcs", "location": f"gs://{bucket}/{name}"}
    raise ValueError(f"Unknown OUTPUT_DESTINATION={destination!r}: use local, bigquery or gcs")
