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

"""fsi-doc-intake orchestrator: package -> reviewed, human-approved record."""

import logging
import os

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai.types import GenerateContentConfig, HttpOptions, HttpRetryOptions

from . import callbacks, prompts, tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
for _noisy in ("httpx", "google.auth", "urllib3", "google_genai.models", "google_genai.types"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

MODEL_NAME = os.getenv("MODEL_NAME") or "gemini-3.8-flash"

root_agent = LlmAgent(
    name="DocIntakeAgent",
    model=Gemini(model=MODEL_NAME),
    generate_content_config=GenerateContentConfig(
        temperature=0,
        http_options=HttpOptions(retry_options=HttpRetryOptions(initial_delay=1, attempts=3)),
    ),
    instruction=prompts.ORCHESTRATOR_PROMPT,
    description="Splits document packages, extracts source-linked fields, runs checks and records human-approved results",
    before_agent_callback=callbacks.route_request,
    before_tool_callback=callbacks.gate_tool_call,
    after_agent_callback=callbacks.llm_judge_gate,
    tools=[
        tools.list_documents,
        tools.process_package,
        tools.check_status,
        tools.get_field_details,
        tools.correct_field,
        tools.accept_fields,
        tools.approve_record,
        tools.reset_package,
    ],
)

# `adk web` names the app after the agent's parent directory ("scripts");
# the App name must match or session creation fails.
app = App(name="scripts", root_agent=root_agent)
