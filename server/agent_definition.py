"""
EdgeDispatch - Agent Definition Module

Architecture:
  - Local Agent: SLM on consumer hardware (llama.cpp). It receives the compact
    MCP manifest, chooses which MCP tools to invoke, and either answers locally
    or calls `escalate_query` to package a structured handoff document.
    The UI-configured threshold is the manual override: if the actual MCP tool
    call count reaches the threshold, the backend escalates even if the model
    did not call `escalate_query`.
  - High-End Agent: Cloud frontier model (OpenAI), receives only structured
    handoff documents for complex synthesis. No MCP tools, no schemas.
  - Handoff: Conditional escalation packaging evidence + rationale.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from pydantic import BaseModel

from agents import (
    Agent,
    OpenAIProvider,
    RunConfig,
    Runner,
    ModelSettings,
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

# Model-driven dispatch instructions. The agent has MCP tools and the
# `escalate_query` function; the UI threshold is the manual routing control.
LOCAL_AGENT_ESCALATE_INSTRUCTIONS = """You are the EdgeDispatch local dispatch agent running on consumer hardware.
You decide which MCP tools are needed. There is no keyword router. The current
manual threshold is {tool_threshold} actual MCP tool call(s).

Your responsibilities:
1. Analyze the user query against the tool manifest below.
2. Invoke only the MCP tools needed to retrieve relevant evidence.
3. For every tool you invoke, record:
     - tool: exact tool name
     - reason: why this tool was needed for the query
     - schema: the tool schema from the manifest
     - result: the returned evidence
4. If you invoked {tool_threshold} or more MCP tools, call `escalate_query` with:
     - query: The original user query
     - selected_tools: JSON array of tool names you invoked
     - rationale: Why cloud synthesis is needed, based on the actual tools invoked
     - evidence_json: JSON array of the evidence objects described above
5. If you invoked fewer than {tool_threshold} MCP tools and have enough evidence,
   synthesize the final answer locally. Cite which tool/source supports each fact.
6. Do not list tools you did not actually invoke.

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
            f"### Tools Invoked\n{tools_str}\n\n"
            f"### Escalation Rationale\n"
            f"Tool threshold was {self.tool_threshold}. "
            f"Handoff includes {len(self.selected_tools)} invoked tool(s): {self.rationale}\n\n"
            f"### Retrieved Evidence\n{evidence_str}\n\n"
            f"Please synthesize a final answer using the evidence above."
        )


# ──────────────────────────────────────────────
# Escalation Function
# ──────────────────────────────────────────────

def make_escalation_function():
    """
    Create the escalation function that the local dispatch agent calls
    to package a structured handoff document for the cloud tier.

    The function stores structured handoff fields in the run context so the
    orchestrator can detect escalation and forward evidence to the high-end
    agent.
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
        context.context["escalated"] = True
        context.context["evidence"] = handoff_input.evidence
        context.context["selected_tools"] = handoff_input.selected_tools
        context.context["rationale"] = handoff_input.rationale

        logger.info(
            "Escalating to high-end agent: %d tools needed, rationale=%s",
            len(handoff_input.selected_tools),
            handoff_input.rationale[:80],
        )
        return (
            f"Handoff prepared. Query requires {len(handoff_input.selected_tools)} tools. "
            f"The high-end synthesis agent will now process the evidence."
        )

    return escalate_query


# ──────────────────────────────────────────────
# Agent Factory Functions
# ──────────────────────────────────────────────

def create_local_dispatch_agent(
    mcp_servers: list[MCPServer],
    tool_threshold: int = DEFAULT_TOOL_THRESHOLD,
    tool_manifest: str = "",
) -> tuple[Agent[dict[str, Any]], OpenAIProvider]:
    """
    Create the local model-driven dispatch agent.

    The agent has MCP tools and the `escalate_query` tool. It can answer
    locally when evidence is sufficient, or package a handoff for cloud
    synthesis when the model or backend threshold requires escalation.
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
        "Created local dispatch agent: model=%s, threshold=%d, mcp_servers=%d",
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

    Dispatch is model-driven: the local SLM chooses MCP tools from the manifest.
    The UI-configured threshold is enforced against actual MCP calls after the
    local run.

    Lifecycle per query:
      1. Reset tracing hooks.
      2. Run the local agent with MCP tools and the escalation tool available.
      3. Count actual MCP tool calls and compare with the UI threshold.
      4. If escalated: package handoff, build escalation sandbox manifest, run the
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
        self.evaluator.launch_phoenix()
        self.evaluator.instrument_openai()

        # Build tool manifest from MCP servers
        self.tool_manifest = self.router.build_manifest_from_servers(mcp_servers)

        # Cost model (pricing synced from the settings store per query)
        self.cost_model = CostModel(
            price_input_per_mtok=DEFAULT_PRICE_INPUT_PER_MTOK,
            price_output_per_mtok=DEFAULT_PRICE_OUTPUT_PER_MTOK,
            manifest_size=len(self.router.manifest),
        )

        # Create the local dispatch agent and the cloud synthesizer.
        self.local_dispatch_agent, self.local_dispatch_provider = create_local_dispatch_agent(
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
        """Process a query and return only the completed orchestrator result."""
        result: OrchestratorResult | None = None
        async for event in self.process_query_stream(query):
            if event.get("event") == "result":
                result = event["result"]

        if result is None:
            return OrchestratorResult(
                final_answer="Error during processing: orchestrator produced no result.",
                was_escalated=False,
                tool_count=0,
                tool_threshold=self.tool_threshold,
                trace_data={"error": "missing_orchestrator_result"},
            )
        return result

    async def process_query_stream(
        self,
        query: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Process a user query and yield final-answer tokens as the active answer
        model produces them.

        The local model chooses tools from the manifest. The backend enforces
        the UI threshold against actual MCP tool calls and escalates when that
        threshold is reached or the model explicitly calls `escalate_query`.

        Yields:
          - {"event": "status", "data": {...}}
          - {"event": "token", "data": "..."}
          - {"event": "result", "result": OrchestratorResult(...)}
        """
        # Reset tracing state for this query (prevents cross-query accumulation).
        self.hooks = EdgeDispatchHooks()

        # Sync pricing + threshold from the settings store (UI may have changed them).
        self._sync_config_from_settings()

        logger.info(
            "Processing query with model-driven dispatch: '%s' (threshold=%d)",
            query[:80],
            self.tool_threshold,
        )

        # Step 1: Build run context with shared state.
        run_context: dict[str, Any] = {
            "tool_threshold": self.tool_threshold,
            "tool_manifest": self.tool_manifest,
            "route_decision": "model_decided",
            "escalated": False,
            "evidence": [],
            "selected_tools": [],
            "rationale": "",
        }

        # Step 2: Run the local model with MCP tools and escalation available.
        run_config = RunConfig(
            model_provider=self.local_dispatch_provider,
            model_settings=ModelSettings(temperature=TEMPERATURE_LOCAL),
            workflow_name="EdgeDispatch Pipeline",
            trace_metadata={
                "tool_threshold": str(self.tool_threshold),
                "routing_mode": "model_driven",
                "architecture": "EdgeDispatch Hybrid",
            },
        )

        try:
            local_result = Runner.run_streamed(
                starting_agent=self.local_dispatch_agent,
                input=query,
                context=run_context,
                max_turns=MAX_TURNS_LOCAL,
                hooks=self.hooks,
                run_config=run_config,
            )
            # The local run decides routing. Its text is buffered so we do not
            # leak an answer that may be superseded by threshold-based cloud
            # synthesis.
            local_answer_parts: list[str] = []
            async for event in local_result.stream_events():
                delta = self._extract_text_delta(event)
                if delta:
                    local_answer_parts.append(delta)
        except Exception as e:
            logger.error("Local agent failed: %s", e)
            final_answer = f"Error during local processing: {e}"
            yield {"event": "token", "data": final_answer}
            yield {"event": "result", "result": OrchestratorResult(
                final_answer=f"Error during local processing: {e}",
                was_escalated=False,
                tool_count=0,
                tool_threshold=self.tool_threshold,
                trace_data={"error": str(e), "route_decision": "local_error"},
            )}
            return

        actual_mcp_calls = self._actual_mcp_tool_calls()
        actual_mcp_count = len(actual_mcp_calls)
        actual_tools = self._infer_local_tools_used()
        model_requested_escalation = bool(run_context.get("escalated"))
        threshold_reached = actual_mcp_count >= self.tool_threshold
        was_escalated = model_requested_escalation or threshold_reached
        route_decision = "escalated" if was_escalated else "local"

        logger.info(
            "Local run complete: route=%s, actual_mcp_calls=%d, threshold=%d, model_requested_escalation=%s",
            route_decision,
            actual_mcp_count,
            self.tool_threshold,
            model_requested_escalation,
        )

        # Step 3: Cloud synthesis when the model requested it or the threshold was reached.
        high_end_hooks: EdgeDispatchHooks | None = None
        handoff_doc: HandoffDocument | None = None
        sandbox_manifest: dict[str, Any] | None = None

        if was_escalated:
            local_output_text = str(local_result.final_output or "".join(local_answer_parts))
            handoff_prompt, selected_tools, evidence, rationale = self._prepare_handoff(
                run_context,
                local_result,
                query,
                actual_tools,
                actual_mcp_count,
                threshold_reached,
                local_output_text=local_output_text,
            )

            # Build the escalation sandbox manifest from tools actually selected/invoked.
            sandbox_manifest = self.router.create_manifest_for_escalation(selected_tools)

            # Run the high-end synthesizer with a fresh hooks instance
            high_end_hooks = EdgeDispatchHooks()
            high_end_config = RunConfig(
                model_provider=self.high_end_provider,
                model_settings=ModelSettings(temperature=TEMPERATURE_HIGH_END),
                workflow_name="EdgeDispatch Pipeline (Escalated)",
                trace_metadata={
                    "tool_threshold": str(self.tool_threshold),
                    "actual_mcp_calls": str(actual_mcp_count),
                    "architecture": "EdgeDispatch Hybrid",
                    "stage": "high_end_synthesis",
                },
            )

            yield {
                "event": "status",
                "data": {
                    "type": "synthesizing",
                    "message": "Streaming cloud synthesis...",
                    "tool_count": actual_mcp_count,
                    "threshold": self.tool_threshold,
                },
            }

            try:
                high_end_result = Runner.run_streamed(
                    starting_agent=self.high_end_agent,
                    input=handoff_prompt,
                    max_turns=MAX_TURNS_HIGH_END,
                    hooks=high_end_hooks,
                    run_config=high_end_config,
                )
                streamed_answer = False
                cloud_answer_parts: list[str] = []
                async for event in high_end_result.stream_events():
                    delta = self._extract_text_delta(event)
                    if delta:
                        streamed_answer = True
                        cloud_answer_parts.append(delta)
                        yield {"event": "token", "data": delta}

                final_answer = str(high_end_result.final_output or "".join(cloud_answer_parts))
                if final_answer and not streamed_answer:
                    yield {"event": "token", "data": final_answer}
            except Exception as e:
                logger.error("High-end agent failed: %s", e)
                final_answer = f"Error during cloud synthesis: {e}"
                yield {"event": "token", "data": final_answer}

            handoff_doc = HandoffDocument(
                query=query,
                selected_tools=selected_tools,
                rationale=rationale,
                evidence=evidence,
                tool_threshold=self.tool_threshold,
            )
        else:
            final_answer = str(local_result.final_output or "".join(local_answer_parts))
            if final_answer:
                yield {"event": "token", "data": final_answer}
            selected_tools = actual_tools
            evidence = self._build_evidence_from_tool_records(query, selected_tools)
            rationale = (
                f"Actual MCP tool call count was {actual_mcp_count}, below the "
                f"threshold of {self.tool_threshold}. Resolved locally."
            )

        # Step 4: Compute the cost breakdown (thesis Eq 2.4-2.6) at current pricing
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

        # Step 5: Evaluate tool selection + answer correctness (thesis Sec 4.6-4.7)
        predicted_tools = selected_tools
        evaluation = self._evaluate(
            query=query,
            answer=final_answer,
            predicted_tools=predicted_tools,
            ground_truth_tools=actual_tools,
            evidence=evidence,
        )

        # Step 6: Log evaluation + assemble trace data
        try:
            self.evaluator.log_evaluation(
                query=query,
                answer=final_answer,
                was_escalated=was_escalated,
                tool_count=actual_mcp_count,
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
            "estimated_tool_count": 0,
            "actual_tool_count": actual_mcp_count,
            "actual_mcp_tool_calls": actual_mcp_calls,
            "total_sdk_tool_count": self.hooks.tool_call_count,
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

        result = OrchestratorResult(
            final_answer=final_answer,
            was_escalated=was_escalated,
            tool_count=actual_mcp_count,
            tool_threshold=self.tool_threshold,
            handoff_document=handoff_doc,
            cost=cost,
            evaluation=evaluation,
            trace_data=trace_data,
        )

        yield {"event": "result", "result": result}

    def set_threshold(self, threshold: int):
        """Dynamically update the tool threshold."""
        self.tool_threshold = threshold
        self.router.threshold = threshold
        logger.info("Tool threshold updated to %d", threshold)

    async def close(self):
        """Release resources held by the orchestrator."""
        await self.local_dispatch_provider.aclose()
        await self.high_end_provider.aclose()

    # ── Internal helpers ──────────────────────

    @staticmethod
    def _extract_text_delta(event: Any) -> str:
        """Return a Responses API text delta from an Agents SDK stream event."""
        if getattr(event, "type", None) != "raw_response_event":
            return ""

        data = getattr(event, "data", None)
        if getattr(data, "type", None) != "response.output_text.delta":
            return ""

        return str(getattr(data, "delta", "") or "")

    def _prepare_handoff(
        self,
        run_context: dict[str, Any],
        local_result: Any,
        query: str,
        actual_tools: list[str],
        actual_mcp_count: int,
        threshold_reached: bool,
        local_output_text: str | None = None,
    ) -> tuple[str, list[str], list[dict[str, Any]], str]:
        """
        Extract the handoff prompt for the cloud tier.

        Primary path: the local SLM called `escalate_query`, storing selected
        tools, rationale, and evidence in run_context.
        Fallback: the UI threshold was reached but the model answered directly.
        In that case, build a handoff from actual tool-call records and the raw
        local output.
        """
        if run_context.get("escalated"):
            model_selected_tools = self._dedupe_tool_names(
                run_context.get("selected_tools", [])
            )
            selected_tools = model_selected_tools or actual_tools
            evidence = self._enrich_evidence(
                run_context.get("evidence", []),
                selected_tools,
                query,
            )
            rationale = run_context.get("rationale") or (
                f"The local model requested escalation after invoking "
                f"{actual_mcp_count} MCP tool call(s)."
            )
            handoff_input = EdgeDispatchHandoffInput(
                query=query,
                selected_tools=selected_tools,
                rationale=rationale,
                evidence=evidence,
                tool_threshold=self.tool_threshold,
            )
            handoff_prompt = handoff_input.to_prompt()
            return handoff_prompt, selected_tools, evidence, rationale

        # Fallback: actual tool count reached the UI threshold but the model
        # answered directly instead of calling escalate_query.
        logger.warning(
            "Actual MCP tool count reached threshold but local SLM did not call "
            "escalate_query; building a handoff from captured tool records."
        )
        raw_output = (
            local_output_text
            if local_output_text is not None
            else str(local_result.final_output or "")
        )
        selected_tools = actual_tools
        evidence = self._build_evidence_from_tool_records(query, selected_tools)
        evidence.append({
            "tool": "local_model_output",
            "reason": (
                "The local model produced a direct answer even though the actual "
                "MCP tool-call threshold was reached."
            ),
            "schema": None,
            "result": raw_output,
        })
        rationale = (
            f"Actual MCP tool call count was {actual_mcp_count}, meeting or "
            f"exceeding the threshold of {self.tool_threshold}. "
            f"Threshold reached: {threshold_reached}. Forwarding captured tool "
            f"evidence and local output for cloud synthesis."
        )
        fallback_input = EdgeDispatchHandoffInput(
            query=query,
            selected_tools=selected_tools,
            rationale=rationale,
            evidence=evidence,
            tool_threshold=self.tool_threshold,
        )
        return fallback_input.to_prompt(), selected_tools, evidence, rationale

    @staticmethod
    def _dedupe_tool_names(tool_names: list[Any]) -> list[str]:
        """Return non-empty tool names in first-seen order."""
        deduped: list[str] = []
        for name in tool_names:
            tool_name = str(name).strip()
            if tool_name and tool_name not in deduped:
                deduped.append(tool_name)
        return deduped

    def _actual_mcp_tool_calls(self) -> list[str]:
        """Return actual MCP tool-call names, preserving repeated calls."""
        return [
            record.tool_name
            for record in self.hooks.tool_call_records
            if record.tool_type == "mcp"
        ]

    def _build_evidence_from_tool_records(
        self,
        query: str,
        selected_tools: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build evidence objects from captured MCP tool-call records."""
        selected = set(selected_tools or [])
        evidence: list[dict[str, Any]] = []
        for record in self.hooks.tool_call_records:
            if record.tool_type != "mcp":
                continue
            if selected and record.tool_name not in selected:
                continue
            evidence.append({
                "tool": record.tool_name,
                "reason": self.router.default_tool_reason(record.tool_name, query),
                "schema": self.router.get_tool_schema(record.tool_name),
                "result": record.result or "Tool completed, but the hook did not capture a result.",
            })
        return evidence

    def _enrich_evidence(
        self,
        evidence: list[dict[str, Any]],
        selected_tools: list[str],
        query: str,
    ) -> list[dict[str, Any]]:
        """Ensure every evidence item carries a reason and invoked-tool schema."""
        enriched: list[dict[str, Any]] = []
        seen_tools: set[str] = set()

        for item in evidence:
            if not isinstance(item, dict):
                enriched.append({
                    "tool": "unknown",
                    "reason": "The local model provided unstructured evidence.",
                    "schema": None,
                    "result": item,
                })
                continue

            tool_name = str(item.get("tool") or "").strip()
            normalized = dict(item)
            if tool_name:
                seen_tools.add(tool_name)
                normalized.setdefault(
                    "reason",
                    self.router.default_tool_reason(tool_name, query),
                )
                canonical_schema = self.router.get_tool_schema(tool_name)
                normalized["schema"] = canonical_schema or normalized.get("schema")
            else:
                normalized.setdefault("tool", "unknown")
                normalized.setdefault(
                    "reason",
                    "The local model did not identify which tool produced this evidence.",
                )
                normalized.setdefault("schema", None)
            enriched.append(normalized)

        for tool_name in selected_tools:
            if tool_name in seen_tools:
                continue
            matching_records = [
                record for record in self.hooks.tool_call_records
                if record.tool_type == "mcp" and record.tool_name == tool_name
            ]
            if matching_records:
                for record in matching_records:
                    enriched.append({
                        "tool": tool_name,
                        "reason": self.router.default_tool_reason(tool_name, query),
                        "schema": self.router.get_tool_schema(tool_name),
                        "result": record.result or "Tool completed, but the hook did not capture a result.",
                    })
            else:
                enriched.append({
                    "tool": tool_name,
                    "reason": self.router.default_tool_reason(tool_name, query),
                    "schema": self.router.get_tool_schema(tool_name),
                    "result": "No captured tool result; the local model selected this tool in its handoff.",
                })

        return enriched

    def _infer_local_tools_used(self) -> list[str]:
        """Best-effort inference of which MCP tools the local dispatch agent invoked."""
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
