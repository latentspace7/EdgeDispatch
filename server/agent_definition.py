"""
EdgeDispatch - Agent Definition Module

Architecture:
  - Local Agent (two variants): SLM on consumer hardware (llama.cpp).
      * resolve variant: used when the dispatcher decides D=0 (local). It
        invokes MCP tools and answers directly; it has no escalation tool.
      * escalate variant: used when the dispatcher decides D=1 (cloud). It
        invokes MCP tools to gather evidence, then calls `escalate_query` to
        package a structured handoff document for the cloud tier.
    The Python dispatch heuristic is authoritative (thesis Eq 2.1): it selects
    which variant runs, so the SLM cannot override the routing decision.
  - High-End Agent: Cloud frontier model (OpenAI), receives only structured
    handoff documents for complex synthesis. No MCP tools, no schemas.
  - Handoff: Conditional escalation packaging evidence + rationale.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from agents import (
    Agent,
    OpenAIProvider,
    RunConfig,
    Runner,
    ModelSettings,
    RunResult,
)
from agents.mcp import MCPServer
from agents import function_tool, RunContextWrapper

from server.mcp_router import MCPRouter, HandoffDocument
from server.observability import EdgeDispatchHooks, ArizeEvaluator
from server.cost import CostModel, CostBreakdown, TokenUsage
from server.config import (
    LOCAL_MODEL_NAME,
    LOCAL_MODEL_BASE_URL,
    LOCAL_MODEL_API_KEY,
    HIGH_END_MODEL_NAME,
    HIGH_END_MODEL_API_KEY,
    HIGH_END_MODEL_BASE_URL,
    DEFAULT_TOOL_THRESHOLD,
    DEFAULT_PRICE_INPUT_PER_MTOK,
    DEFAULT_PRICE_OUTPUT_PER_MTOK,
    MAX_TURNS_LOCAL,
    MAX_TURNS_HIGH_END,
    TEMPERATURE_LOCAL,
    TEMPERATURE_HIGH_END,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Model Providers
# ──────────────────────────────────────────────

def create_local_model_provider() -> OpenAIProvider:
    """Create a model provider for the local llama.cpp server (OpenAI-compatible API)."""
    return OpenAIProvider(
        api_key=LOCAL_MODEL_API_KEY,
        base_url=LOCAL_MODEL_BASE_URL,
    )


def create_high_end_model_provider() -> OpenAIProvider:
    """Create a model provider for the high-end cloud model (OpenAI)."""
    return OpenAIProvider(
        api_key=HIGH_END_MODEL_API_KEY,
        base_url=HIGH_END_MODEL_BASE_URL,
    )


# ──────────────────────────────────────────────
# Agent Instructions
# ──────────────────────────────────────────────

# Used when the dispatcher has decided D=0 (local resolution). The agent has
# MCP tools but NO escalation tool. It cannot override the routing decision.
LOCAL_AGENT_RESOLVE_INSTRUCTIONS = """You are the EdgeDispatch local dispatch agent running on consumer hardware.
The dispatcher has determined this query can be resolved LOCALLY (tool requirement
is below the escalation threshold of {tool_threshold}).

Your responsibilities:
1. Analyze the user query against the tool manifest below.
2. Invoke the necessary MCP tools to retrieve the required information.
3. Synthesize a complete, well-reasoned answer directly to the user using the
   retrieved evidence. Cite which tool/source each fact came from.
4. You CANNOT escalate — no escalation tool is available. Answer fully here.

Tool Manifest (compact descriptions of available tools):
{tool_manifest}

If a tool returns no useful evidence, say so honestly rather than fabricating.
"""

# Used when the dispatcher has decided D=1 (cloud escalation). The agent has
# MCP tools AND the escalate_query tool. It gathers evidence, then hands off.
LOCAL_AGENT_ESCALATE_INSTRUCTIONS = """You are the EdgeDispatch local dispatch agent running on consumer hardware.
The dispatcher has determined this query REQUIRES cloud synthesis (tool requirement
meets or exceeds the escalation threshold of {tool_threshold}).

Your responsibilities:
1. Analyze the user query against the tool manifest below.
2. Invoke the necessary MCP tools to gather all relevant evidence.
3. Once evidence is collected, call the `escalate_query` tool with:
     - query: The original user query
     - selected_tools: JSON array of tool names you invoked
     - rationale: Why cloud synthesis is needed
     - evidence_json: JSON array of the evidence objects retrieved from the tools
4. Do NOT attempt to write the final answer yourself — the cloud synthesis agent
   will produce the final answer from your handoff.

Tool Manifest (compact descriptions of available tools):
{tool_manifest}
"""

HIGH_END_AGENT_INSTRUCTIONS = """You are the EdgeDispatch high-end synthesis agent, a frontier cloud model.
You receive structured handoff documents from the local dispatch agent containing:
  - The original user query
  - The specific tools that were invoked
  - The rationale for escalation
  - Evidence retrieved from MCP tools

Your task is to synthesize a comprehensive, accurate answer using ONLY the provided
evidence. Do not fabricate information. If the evidence is insufficient, state so clearly.

You do NOT have direct access to MCP tools - all necessary evidence is already
provided in the handoff document.
"""


# ──────────────────────────────────────────────
# Handoff Input Schema
# ──────────────────────────────────────────────

class EdgeDispatchHandoffInput(BaseModel):
    """Structured handoff data passed from local agent to high-end agent.

    Pydantic model (not a dataclass) so the openai-agents SDK can generate a
    strict JSON schema when this is used with function_tool().
    """
    query: str
    selected_tools: list[str]
    rationale: str
    evidence: list[dict[str, Any]]
    tool_threshold: int

    def to_prompt(self) -> str:
        """Format the handoff data as a structured prompt for the high-end agent."""
        evidence_str = json.dumps(self.evidence, indent=2, default=str)
        tools_str = ", ".join(self.selected_tools)

        return (
            f"## Handoff from EdgeDispatch Local Agent\n\n"
            f"### Original Query\n{self.query}\n\n"
            f"### Selected Tools\n{tools_str}\n\n"
            f"### Escalation Rationale\n"
            f"Tool threshold was {self.tool_threshold}. "
            f"Query required {len(self.selected_tools)} tool(s): {self.rationale}\n\n"
            f"### Retrieved Evidence\n{evidence_str}\n\n"
            f"Please synthesize a final answer using the evidence above."
        )


# ──────────────────────────────────────────────
# Escalation Function
# ──────────────────────────────────────────────

def make_escalation_function():
    """
    Create the escalation function that the escalate-variant local agent calls
    to package a structured handoff document for the cloud tier.

    The function stores the handoff prompt in the run context so the orchestrator
    can detect escalation and forward to the high-end agent.
    """
    async def escalate_query(
        context: RunContextWrapper[dict[str, Any]],
        query: str,
        selected_tools: str,
        rationale: str,
        evidence_json: str,
    ) -> str:
        """Escalate a complex query to the high-end cloud model.

        Call this after gathering evidence from MCP tools, when the dispatcher
        has routed the query to the cloud tier.

        Args:
            query: The original user query
            selected_tools: JSON array of tool names used
            rationale: Why escalation is necessary
            evidence_json: JSON array of evidence objects retrieved from tools
        """
        tools_list = json.loads(selected_tools)
        evidence = json.loads(evidence_json)

        handoff_input = EdgeDispatchHandoffInput(
            query=query,
            selected_tools=tools_list,
            rationale=rationale,
            evidence=evidence,
            tool_threshold=context.context.get("tool_threshold", DEFAULT_TOOL_THRESHOLD),
        )

        # Store handoff data in context for the orchestrator to detect
        context.context["handoff_document"] = handoff_input.to_prompt()
        context.context["escalated"] = True
        context.context["evidence"] = evidence
        context.context["selected_tools"] = tools_list
        context.context["rationale"] = rationale

        logger.info(
            "Escalating to high-end agent: %d tools needed, rationale=%s",
            len(tools_list),
            rationale[:80],
        )
        return (
            f"Handoff prepared. Query requires {len(tools_list)} tools. "
            f"The high-end synthesis agent will now process the evidence."
        )

    return escalate_query


# ──────────────────────────────────────────────
# Agent Factory Functions
# ──────────────────────────────────────────────

def create_local_resolve_agent(
    mcp_servers: list[MCPServer],
    tool_threshold: int = DEFAULT_TOOL_THRESHOLD,
    tool_manifest: str = "",
) -> tuple[Agent[dict[str, Any]], OpenAIProvider]:
    """
    Create the local agent variant used when the dispatcher routes locally (D=0).

    This agent has MCP tools but NO escalation tool. It must answer directly.
    """
    model_provider = create_local_model_provider()
    model_settings = ModelSettings(temperature=TEMPERATURE_LOCAL)

    instructions = LOCAL_AGENT_RESOLVE_INSTRUCTIONS.format(
        tool_threshold=tool_threshold,
        tool_manifest=tool_manifest,
    )

    agent = Agent(
        name="EdgeDispatch Local Agent",
        handoff_description="Local dispatch agent that resolves queries entirely at the edge",
        instructions=instructions,
        model=LOCAL_MODEL_NAME,
        model_settings=model_settings,
        mcp_servers=mcp_servers,
        tool_use_behavior="run_llm_again",
    )

    logger.info(
        "Created local resolve agent: model=%s, threshold=%d, mcp_servers=%d",
        LOCAL_MODEL_NAME,
        tool_threshold,
        len(mcp_servers),
    )
    return agent, model_provider


def create_local_escalate_agent(
    mcp_servers: list[MCPServer],
    tool_threshold: int = DEFAULT_TOOL_THRESHOLD,
    tool_manifest: str = "",
) -> tuple[Agent[dict[str, Any]], OpenAIProvider]:
    """
    Create the local agent variant used when the dispatcher routes to cloud (D=1).

    This agent has MCP tools AND the escalate_query tool. It gathers evidence
    then hands off to the cloud synthesizer.
    """
    model_provider = create_local_model_provider()
    model_settings = ModelSettings(temperature=TEMPERATURE_LOCAL)

    instructions = LOCAL_AGENT_ESCALATE_INSTRUCTIONS.format(
        tool_threshold=tool_threshold,
        tool_manifest=tool_manifest,
    )

    escalate_tool = function_tool(
        make_escalation_function(),
        name_override="escalate_query",
        description_override=(
            "Escalate a query that requires cloud-tier synthesis. Provide: query, "
            "selected_tools (JSON array of tool names), rationale (string), and "
            "evidence_json (JSON array of evidence objects from tool calls)."
        ),
    )

    agent = Agent(
        name="EdgeDispatch Local Agent",
        handoff_description="Local dispatch agent that gathers evidence and escalates to the cloud tier",
        instructions=instructions,
        model=LOCAL_MODEL_NAME,
        model_settings=model_settings,
        mcp_servers=mcp_servers,
        tools=[escalate_tool],
        tool_use_behavior="run_llm_again",
    )

    logger.info(
        "Created local escalate agent: model=%s, threshold=%d, mcp_servers=%d",
        LOCAL_MODEL_NAME,
        tool_threshold,
        len(mcp_servers),
    )
    return agent, model_provider


def create_high_end_agent() -> tuple[Agent[dict[str, Any]], OpenAIProvider]:
    """
    Create the high-end synthesis agent backed by a cloud frontier model (OpenAI).

    Pure synthesizer with no MCP servers and no tools. Receives only the handoff prompt.
    """
    model_provider = create_high_end_model_provider()
    model_settings = ModelSettings(temperature=TEMPERATURE_HIGH_END)

    agent = Agent(
        name="EdgeDispatch High-End Agent",
        handoff_description="Cloud synthesis agent that handles complex multi-tool queries",
        instructions=HIGH_END_AGENT_INSTRUCTIONS,
        model=HIGH_END_MODEL_NAME,
        model_settings=model_settings,
        tool_use_behavior="run_llm_again",
    )

    logger.info("Created high-end agent: model=%s", HIGH_END_MODEL_NAME)
    return agent, model_provider


# ──────────────────────────────────────────────
# EdgeDispatch Orchestrator
# ──────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    """Result from the EdgeDispatch orchestrator."""
    final_answer: str
    was_escalated: bool
    tool_count: int
    tool_threshold: int
    handoff_document: HandoffDocument | None = None
    cost: CostBreakdown | None = None
    evaluation: dict[str, Any] = field(default_factory=dict)
    trace_data: dict[str, Any] = field(default_factory=dict)


class EdgeDispatchOrchestrator:
    """
    Main orchestrator for the EdgeDispatch hybrid architecture.

    The Python dispatch heuristic (MCPRouter) is authoritative (thesis Eq 2.1):
    it decides D in {0,1} and selects which local-agent variant runs. This makes
    routing deterministic and decouples dispatch correctness from SLM compliance.

    Lifecycle per query:
      1. Reset tracing hooks.
      2. Analyze the query -> (D, required_tools) via MCPRouter.
      3. Run the resolve-variant (D=0) or escalate-variant (D=1) local agent.
      4. If D=1: package handoff, build escalation sandbox manifest, run the
         high-end synthesizer with the handoff prompt.
      5. Compute the cost breakdown (thesis Eq 2.4-2.6) at current UI pricing.
      6. Evaluate tool selection + answer correctness (thesis Sec 4.6-4.7).
      7. Log evaluation + assemble trace data.
    """
    def __init__(
        self,
        mcp_servers: list[MCPServer],
        tool_threshold: int = DEFAULT_TOOL_THRESHOLD,
        arize_endpoint: str | None = None,
        arize_api_key: str | None = None,
    ):
        self.tool_threshold = tool_threshold
        self.mcp_servers = mcp_servers
        self.router = MCPRouter(threshold=tool_threshold)
        self.hooks = EdgeDispatchHooks()
        self.evaluator = ArizeEvaluator(
            endpoint=arize_endpoint,
            api_key=arize_api_key,
        )

        # Wire observability: instrument the OpenAI client for OpenInference
        # spans and auto-launch the Phoenix debug UI.
        self.evaluator.instrument_openai()
        self.evaluator.launch_phoenix()

        # Build tool manifest from MCP servers
        self.tool_manifest = self.router.build_manifest_from_servers(mcp_servers)

        # Cost model (pricing synced from the settings store per query)
        self.cost_model = CostModel(
            price_input_per_mtok=DEFAULT_PRICE_INPUT_PER_MTOK,
            price_output_per_mtok=DEFAULT_PRICE_OUTPUT_PER_MTOK,
            manifest_size=len(self.router.manifest),
        )

        # Create both local-agent variants + the cloud synthesizer
        self.local_resolve_agent, self.local_resolve_provider = create_local_resolve_agent(
            mcp_servers=mcp_servers,
            tool_threshold=tool_threshold,
            tool_manifest=self.tool_manifest,
        )
        self.local_escalate_agent, self.local_escalate_provider = create_local_escalate_agent(
            mcp_servers=mcp_servers,
            tool_threshold=tool_threshold,
            tool_manifest=self.tool_manifest,
        )
        self.high_end_agent, self.high_end_provider = create_high_end_agent()

        # Publish the connected MCP server count to the settings store
        self._sync_mcp_server_count()

        logger.info(
            "EdgeDispatchOrchestrator initialized: threshold=%d, manifest_entries=%d, mcp_servers=%d",
            tool_threshold,
            len(self.router.manifest),
            len(mcp_servers),
        )

    # ── Public API ────────────────────────────

    async def process_query(self, query: str) -> OrchestratorResult:
        """
        Process a user query through the EdgeDispatch pipeline (Algorithm 1).

        The dispatch heuristic is authoritative: it selects the local-agent
        variant and therefore the route. Cost is computed at the UI-configured
        pricing; evaluation metrics are computed per query.
        """
        # Reset tracing state for this query (prevents cross-query accumulation).
        self.hooks = EdgeDispatchHooks()

        # Sync pricing + threshold from the settings store (UI may have changed them).
        self._sync_config_from_settings()

        # Step 1: Authoritative dispatch decision (thesis Eq 2.1)
        tool_analysis = self.router.analyze_query(query, self.tool_manifest)
        estimated_tools = tool_analysis["estimated_tool_count"]
        required_tools = tool_analysis.get("required_tools", [])
        route_decision = tool_analysis["route_decision"]
        was_escalated = route_decision == "escalated"

        logger.info(
            "Query analysis: '%s' -> route=%s, %d tools estimated, threshold=%d",
            query[:80],
            route_decision,
            estimated_tools,
            self.tool_threshold,
        )

        # Step 2: Build run context with shared state
        run_context: dict[str, Any] = {
            "tool_threshold": self.tool_threshold,
            "tool_manifest": self.tool_manifest,
            "estimated_tools": estimated_tools,
            "required_tools": required_tools,
            "route_decision": route_decision,
            "escalated": False,
            "handoff_document": None,
            "evidence": [],
            "selected_tools": [],
            "rationale": "",
        }

        # Step 3: Run the appropriate local-agent variant
        starting_agent = (
            self.local_escalate_agent if was_escalated else self.local_resolve_agent
        )
        local_provider = (
            self.local_escalate_provider if was_escalated else self.local_resolve_provider
        )

        run_config = RunConfig(
            model_provider=local_provider,
            model_settings=ModelSettings(temperature=TEMPERATURE_LOCAL),
            workflow_name="EdgeDispatch Pipeline",
            trace_metadata={
                "tool_threshold": str(self.tool_threshold),
                "estimated_tools": str(estimated_tools),
                "route_decision": route_decision,
                "architecture": "EdgeDispatch Hybrid",
            },
        )

        try:
            result: RunResult = await Runner.run(
                starting_agent=starting_agent,
                input=query,
                context=run_context,
                max_turns=MAX_TURNS_LOCAL,
                hooks=self.hooks,
                run_config=run_config,
            )
        except Exception as e:
            logger.error("Local agent failed: %s", e)
            return OrchestratorResult(
                final_answer=f"Error during local processing: {e}",
                was_escalated=False,
                tool_count=0,
                tool_threshold=self.tool_threshold,
                trace_data={"error": str(e), "route_decision": route_decision},
            )

        # Step 4: Cloud synthesis (only when the dispatcher routed D=1)
        high_end_hooks: EdgeDispatchHooks | None = None
        handoff_doc: HandoffDocument | None = None
        sandbox_manifest: dict[str, Any] | None = None

        if was_escalated:
            handoff_prompt, selected_tools, evidence, rationale = self._prepare_handoff(
                run_context, result, query, required_tools, tool_analysis
            )

            # Build the escalation sandbox manifest (only the needed tools in scope)
            sandbox_manifest = self.router.create_manifest_for_escalation(required_tools)

            # Run the high-end synthesizer with a fresh hooks instance
            high_end_hooks = EdgeDispatchHooks()
            high_end_config = RunConfig(
                model_provider=self.high_end_provider,
                model_settings=ModelSettings(temperature=TEMPERATURE_HIGH_END),
                workflow_name="EdgeDispatch Pipeline (Escalated)",
                trace_metadata={
                    "tool_threshold": str(self.tool_threshold),
                    "estimated_tools": str(estimated_tools),
                    "architecture": "EdgeDispatch Hybrid",
                    "stage": "high_end_synthesis",
                },
            )

            try:
                high_end_result: RunResult = await Runner.run(
                    starting_agent=self.high_end_agent,
                    input=handoff_prompt,
                    max_turns=MAX_TURNS_HIGH_END,
                    hooks=high_end_hooks,
                    run_config=high_end_config,
                )
                final_answer = high_end_result.final_output
            except Exception as e:
                logger.error("High-end agent failed: %s", e)
                final_answer = f"Error during cloud synthesis: {e}"

            handoff_doc = HandoffDocument(
                query=query,
                selected_tools=selected_tools,
                rationale=rationale,
                evidence=evidence,
                tool_threshold=self.tool_threshold,
            )
        else:
            final_answer = result.final_output
            selected_tools = []
            evidence = []
            rationale = tool_analysis.get("rationale", "")

        # Step 5: Compute the cost breakdown (thesis Eq 2.4-2.6) at current pricing
        local_usage = self.hooks.get_token_usage()
        cloud_usage = high_end_hooks.get_token_usage() if high_end_hooks else TokenUsage()
        handoff_text = handoff_doc.to_compact_prompt() if handoff_doc else ""

        cost = self.cost_model.compute(
            route="escalated" if was_escalated else "local",
            query=query,
            evidence=evidence,
            handoff_text=handoff_text,
            cloud_usage=cloud_usage,
            local_usage=local_usage,
        )

        # Step 6: Evaluate tool selection + answer correctness (thesis Sec 4.6-4.7)
        predicted_tools = selected_tools if was_escalated else self._infer_local_tools_used()
        evaluation = self._evaluate(
            query=query,
            answer=final_answer,
            predicted_tools=predicted_tools,
            ground_truth_tools=required_tools,
            evidence=evidence,
        )

        # Step 7: Log evaluation + assemble trace data
        try:
            self.evaluator.log_evaluation(
                query=query,
                answer=final_answer,
                was_escalated=was_escalated,
                tool_count=self.hooks.tool_call_count,
                threshold=self.tool_threshold,
                trace_data={
                    "cost": cost.as_dict(),
                    "evaluation": evaluation,
                },
            )
        except Exception as e:
            logger.warning("Arize evaluation logging failed (non-fatal): %s", e)

        trace_data = {
            "query": query,
            "estimated_tool_count": estimated_tools,
            "actual_tool_count": self.hooks.tool_call_count,
            "tool_threshold": self.tool_threshold,
            "was_escalated": was_escalated,
            "route_decision": route_decision,
            "local_turns": self.hooks.local_turn_count,
            "high_end_turns": high_end_hooks.local_turn_count + high_end_hooks.high_end_turn_count
            if high_end_hooks
            else 0,
            "cost": cost.as_dict(),
            "evaluation": evaluation,
            "sandbox_manifest": sandbox_manifest,
        }

        return OrchestratorResult(
            final_answer=final_answer,
            was_escalated=was_escalated,
            tool_count=self.hooks.tool_call_count,
            tool_threshold=self.tool_threshold,
            handoff_document=handoff_doc,
            cost=cost,
            evaluation=evaluation,
            trace_data=trace_data,
        )

    def set_threshold(self, threshold: int):
        """Dynamically update the tool threshold."""
        self.tool_threshold = threshold
        self.router.threshold = threshold
        logger.info("Tool threshold updated to %d", threshold)

    async def close(self):
        """Release resources held by the orchestrator."""
        await self.local_resolve_provider.aclose()
        await self.local_escalate_provider.aclose()
        await self.high_end_provider.aclose()

    # ── Internal helpers ──────────────────────

    def _prepare_handoff(
        self,
        run_context: dict[str, Any],
        local_result: RunResult,
        query: str,
        required_tools: list[str],
        tool_analysis: dict[str, Any],
    ) -> tuple[str, list[str], list[dict[str, Any]], str]:
        """
        Extract the handoff prompt for the cloud tier.

        Primary path: the escalate-variant SLM called `escalate_query`, which
        stored the handoff prompt in run_context.
        Fallback: if the SLM did not call escalate_query (non-compliance with
        the dispatcher's D=1 decision), build a minimal handoff from the query
        and the SLM's raw final output, and log a warning.
        """
        if run_context.get("escalated") and run_context.get("handoff_document"):
            handoff_prompt = run_context["handoff_document"]
            selected_tools = run_context.get("selected_tools", required_tools)
            evidence = run_context.get("evidence", [])
            rationale = run_context.get("rationale", tool_analysis.get("rationale", ""))
            return handoff_prompt, selected_tools, evidence, rationale

        # Fallback: SLM was told to escalate but answered directly instead.
        logger.warning(
            "Dispatch routed D=1 but local SLM did not call escalate_query; "
            "building a minimal handoff from the SLM's raw output."
        )
        raw_output = str(local_result.final_output or "")
        evidence = [{"tool": "local_slm_fallback", "result": raw_output}]
        selected_tools = required_tools
        rationale = (
            f"Fallback handoff: dispatcher routed D=1 but the local SLM did not "
            f"call escalate_query. Forwarding raw SLM output as evidence. "
            f"{tool_analysis.get('rationale', '')}"
        )
        fallback_input = EdgeDispatchHandoffInput(
            query=query,
            selected_tools=selected_tools,
            rationale=rationale,
            evidence=evidence,
            tool_threshold=self.tool_threshold,
        )
        return fallback_input.to_prompt(), selected_tools, evidence, rationale

    def _infer_local_tools_used(self) -> list[str]:
        """Best-effort inference of which MCP tools the resolve agent invoked."""
        used: list[str] = []
        for record in self.hooks.tool_call_records:
            if record.tool_type == "mcp" and record.tool_name not in used:
                used.append(record.tool_name)
        return used

    def _evaluate(
        self,
        query: str,
        answer: str,
        predicted_tools: list[str],
        ground_truth_tools: list[str],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run tool-selection + answer-correctness evaluation for this query."""
        try:
            tool_eval = self.evaluator.evaluate_tool_selection(
                predicted_tools=predicted_tools,
                ground_truth_tools=ground_truth_tools,
            )
        except Exception as e:
            logger.warning("Tool selection evaluation failed: %s", e)
            tool_eval = {"error": str(e)}

        try:
            answer_eval = self.evaluator.evaluate_answer_correctness(
                answer=answer,
                evidence=evidence,
                query=query,
            )
        except Exception as e:
            logger.warning("Answer correctness evaluation failed: %s", e)
            answer_eval = {"error": str(e)}

        return {"tool_selection": tool_eval, "answer_correctness": answer_eval}

    def _sync_config_from_settings(self) -> None:
        """Pull the latest threshold + pricing from the settings store (UI edits)."""
        try:
            from server.routes.settings import get_current_threshold, get_current_pricing

            threshold = get_current_threshold()
            if threshold != self.tool_threshold:
                self.set_threshold(threshold)

            p_in, p_out = get_current_pricing()
            self.cost_model.update_pricing(p_in, p_out)
        except Exception as e:
            logger.debug("Could not sync config from settings store: %s", e)

    def _sync_mcp_server_count(self) -> None:
        """Publish the connected MCP server count to the settings store."""
        try:
            from server.routes.settings import set_mcp_server_count

            set_mcp_server_count(len(self.mcp_servers))
        except Exception as e:
            logger.debug("Could not publish mcp_server_count: %s", e)
