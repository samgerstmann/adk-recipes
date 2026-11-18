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

"""Shared utilities for the skill setup script.

Vendored locally so this skill ships as a self-contained package.
"""

import pathlib
import re
from typing import Any

import yaml  # type: ignore[import-untyped]


class SpecError(ValueError):
    """design-spec.md is missing or malformed."""


def load_config(config_path: str | pathlib.Path) -> dict[str, Any]:
    """Load the YAML frontmatter of design-spec.md.

    Raises SpecError if the file is missing or has no frontmatter (it was
    rewritten instead of edited).
    """
    path = pathlib.Path(config_path)
    if not path.exists():
        raise SpecError(f"{path} not found: run bootstrap.sh first.")
    match = re.match(r"^---\s*\n(.*?)\n---", path.read_text(encoding="utf-8"), re.S)
    if not match:
        raise SpecError(f"{path} has no YAML frontmatter: edit the template values, do not rewrite it.")
    return yaml.safe_load(match.group(1)) or {}


def example_defaults(example: pathlib.Path) -> dict[str, str]:
    """Non-placeholder KEY=VALUE pairs of a .env.example file."""
    out: dict[str, str] = {}
    if not example.exists():
        return out
    for line in example.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#") and value.strip() and not value.startswith("<"):
            out[key.strip()] = value.strip()
    return out


def merge_env(env_path: pathlib.Path, values: dict[str, Any], defaults: dict[str, str]) -> None:
    """Write values into env_path, keeping unrelated lines; add missing defaults."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    kept = [ln for ln in lines if ln.split("=", 1)[0].strip() not in values]
    have = {ln.split("=", 1)[0].strip() for ln in kept} | set(values)
    kept += [f"{k}={v}" for k, v in values.items()]
    kept += [f"{k}={v}" for k, v in defaults.items() if k not in have]
    env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
