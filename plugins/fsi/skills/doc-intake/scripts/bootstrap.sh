#!/usr/bin/env bash
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

# Workspace bootstrap: run from the workspace (cwd). Creates .venv with the
# skill installed editable and copies the design-spec template.
# SKILL_EXTRAS=gcp adds Firestore + GCS clients (Full setup).
set -euo pipefail

SKILL_NAME="fsi-small-business-loan"
SKILL_DIR="${SKILL_DIR:-}"
if [ -z "$SKILL_DIR" ]; then
  SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
if [ ! -f "$SKILL_DIR/SKILL.md" ]; then
  echo "ERROR: $SKILL_NAME is not installed (no SKILL.md in $SKILL_DIR)." >&2
  exit 1
fi
TARGET="$SKILL_DIR"
if [ -n "${SKILL_EXTRAS:-}" ]; then
  TARGET="$SKILL_DIR[$SKILL_EXTRAS]"
fi

# Prefer uv (fast, brings its own Python); fall back to python3 -m venv + pip.
if command -v uv >/dev/null 2>&1; then
  [ -x .venv/bin/python ] || uv venv --quiet --python ">=3.11" .venv
  uv pip install --quiet --python .venv/bin/python -e "$TARGET"
else
  PYTHON_BIN="${PYTHON_BIN:-}"
  if [ -z "$PYTHON_BIN" ]; then
    for py in python3.13 python3.12 python3.11 python3; do
      if command -v "$py" >/dev/null 2>&1 && "$py" -c \
          'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 11))'
      then
        PYTHON_BIN="$py"
        break
      fi
    done
  fi
  if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: need uv, or Python 3.11+ with venv (set PYTHON_BIN)." >&2
    exit 1
  fi
  [ -x .venv/bin/python ] || "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e "$TARGET"
fi
[ -f ./design-spec.md ] || cp "$SKILL_DIR/assets/design-spec.md" ./design-spec.md

echo "READY"
echo "  SKILL_DIR=$SKILL_DIR"
echo "  workspace=$(pwd)"
echo "  design-spec=$(pwd)/design-spec.md"
