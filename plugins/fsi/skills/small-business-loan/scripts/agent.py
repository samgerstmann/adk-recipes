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

"""Small business loan orchestrator with 4 sub-agents.

1. DocumentExtractionAgent - extracts data from the application document
2. UnderwritingAgent       - validates data, checks eligibility rules
3. PricingAgent            - calculates rate and payment terms
4. LoanDecisionAgent       - finalizes the decision after human approval

Adapted from contrib/python/small-business-loan-agent.
"""

import logging
import os

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.planners import BuiltInPlanner
from google.adk.tools.agent_tool import AgentTool
from google.genai.types import GenerateContentConfig, HttpOptions, HttpRetryOptions, ThinkingConfig

from . import callbacks, prompts, tools
from .models import LoanApplicationData, LoanDecisionResult, PricingResult, UnderwritingReport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
for _noisy in ("httpx", "google.auth", "urllib3", "google_genai.models", "google_genai.types"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

MODEL_NAME = os.getenv("MODEL_NAME") or "gemini-3.8-flash"


def _model() -> Gemini:
    return Gemini(model=MODEL_NAME)


def _config() -> GenerateContentConfig:
    return GenerateContentConfig(
        http_options=HttpOptions(retry_options=HttpRetryOptions(initial_delay=1, attempts=3)),
    )


def _sub_agent(name: str, description: str, instruction: str, schema, agent_tools=(), **kwargs) -> LlmAgent:
    if agent_tools:
        kwargs.setdefault("before_model_callback", callbacks.single_tool_call)
    return LlmAgent(
        name=name,
        model=_model(),
        generate_content_config=_config(),
        instruction=instruction,
        description=description,
        tools=list(agent_tools),
        before_agent_callback=callbacks.check_step_prerequisites,
        after_agent_callback=callbacks.log_step_result,
        after_model_callback=callbacks.trace_model_response,
        output_schema=schema,
        output_key=f"{name}_output",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        **kwargs,
    )


document_extraction_agent = _sub_agent(
    "DocumentExtractionAgent",
    "Extracts structured loan application data from the selected application document",
    prompts.DOCUMENT_EXTRACTION_PROMPT,
    LoanApplicationData,
    before_model_callback=callbacks.inject_document,
)
underwriting_agent = _sub_agent(
    "UnderwritingAgent",
    "Validates application data against internal records and checks eligibility",
    prompts.UNDERWRITING_PROMPT,
    UnderwritingReport,
    [tools.get_internal_business_data],
)
pricing_agent = _sub_agent(
    "PricingAgent",
    "Calculates interest rate and payment terms based on risk assessment",
    prompts.PRICING_PROMPT,
    PricingResult,
    [tools.calculate_loan_pricing],
)
loan_decision_agent = _sub_agent(
    "LoanDecisionAgent",
    "Finalizes the loan decision and decision letter. Requires explicit human approval.",
    prompts.LOAN_DECISION_PROMPT,
    LoanDecisionResult,
    [tools.finalize_loan_decision],
)

root_agent = LlmAgent(
    name="SmallBusinessLoanOrchestratorAgent",
    model=_model(),
    generate_content_config=_config(),
    instruction=prompts.ORCHESTRATOR_PROMPT,
    description="Orchestrates small business loan processing with human approval and repair & resume",
    planner=BuiltInPlanner(thinking_config=ThinkingConfig(include_thoughts=False)),
    before_agent_callback=callbacks.route_request,
    before_tool_callback=callbacks.gate_tool_call,
    after_tool_callback=callbacks.after_tool_call,
    after_agent_callback=callbacks.llm_judge_gate,
    tools=[
        tools.list_applications,
        tools.select_application,
        tools.check_process_status,
        tools.repair_application_field,
        tools.reset_demo_application,
        AgentTool(document_extraction_agent),
        AgentTool(underwriting_agent),
        AgentTool(pricing_agent),
        AgentTool(loan_decision_agent),
    ],
)

# `adk web` names the app after the agent's parent directory ("scripts");
# the App name must match or session creation fails.
app = App(name="scripts", root_agent=root_agent)
