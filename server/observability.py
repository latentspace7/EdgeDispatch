"""
EdgeDispatch - Observability Module

Integrates Arize AI (Phoenix / OpenInference ecosystem) for:
  - End-to-end tracing throughout the execution pipeline
  - Recording tool-call counts and routing decisions (local vs. escalated)
  - Agent lifecycle hooks (start, turn-taking, handoff events)
  - Evaluation of tool selection decisions and final answer correctness

Based on the thesis Section 4.6 evaluation framework requirements.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agents import (
    RunHooks,
    RunContextWrapper,
    AgentHookContext,
    Agent,
    Tool,
    FunctionTool,
    HostedMCPTool,
)
from agents.items import ModelResponse

from server.config import (
    ARIZE_PHOENIX_ENDPOINT,
    ARIZE_API_KEY,
    ARIZE_PROJECT_NAME,
    LOG_LEVEL,
)

logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))


# ──────────────────────────────────────────────
# Span / Trace Data Models
# ──────────────────────────────────────────────

@dataclass
class ToolCallRecord:
    """Record of a single tool call for tracing."""
    tool_name: str
    tool_type: str  # "function", "mcp", "handoff"
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    duration_ms: float = 0.0
    error: str | None = None


@dataclass
class AgentTurnRecord:
    """Record of an agent turn (LLM call) for tracing."""
    agent_name: str
    turn_number: int
    timestamp: float = field(default_factory=time.time)
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls_made: int = 0


@dataclass
class RouteDecisionRecord:
    """Record of the routing decision for a query."""
    query: str
    decision: str  # "local" or "escalated"
    estimated_tool_count: int
    actual_tool_count: int
    threshold: int
    timestamp: float = field(default_factory=time.time)


# ──────────────────────────────────────────────
# EdgeDispatch Tracing Hooks
# ──────────────────────────────────────────────

class EdgeDispatchHooks(RunHooks[dict[str, Any]]):
    """
    Custom RunHooks implementation for EdgeDispatch end-to-end tracing.

    Tracks:
      - Agent lifecycle: start, end, and turn events
      - Tool call counts with success/failure recording
      - Routing decisions (local vs. escalated)

    Note: Escalation in EdgeDispatch is driven by the `escalate_query` function
    tool (set in run context), not by the SDK's `handoff()` mechanism, so the
    `on_handoff` hook is intentionally not overridden here.
    """
    def __init__(self):
        super().__init__()
        self.tool_call_count: int = 0
        self.local_turn_count: int = 0
        self.high_end_turn_count: int = 0
        self.tool_call_records: list[ToolCallRecord] = []
        self.turn_records: list[AgentTurnRecord] = []
        self._current_tool_start: dict[str, float] = {}

    # ── Agent Lifecycle Hooks ─────────────────

    async def on_agent_start(
        self, context: AgentHookContext[dict[str, Any]], agent: Agent[Any]
    ):
        """Called when an agent starts processing."""
        self.tool_call_count = 0  # Reset per-agent
        logger.info(
            "Agent started: name=%s, type=%s",
            agent.name,
            "local" if "local" in agent.name.lower() else "high_end",
        )

    async def on_agent_end(
        self,
        context: AgentHookContext[dict[str, Any]],
        agent: Agent[Any],
        output: Any,
    ):
        """Called when an agent finishes processing."""
        # Determine which agent's turn counter to update
        if "local" in agent.name.lower():
            self.local_turn_count += 1
        else:
            self.high_end_turn_count += 1

        logger.info(
            "Agent finished: name=%s, tool_calls=%d",
            agent.name,
            self.tool_call_count,
        )

    # ── Tool Lifecycle Hooks ──────────────────

    async def on_tool_start(
        self,
        context: RunContextWrapper[dict[str, Any]],
        agent: Agent[Any],
        tool: Tool,
    ):
        """Called when a tool invocation begins."""
        self.tool_call_count += 1
        tool_name = getattr(tool, "name", "unknown_tool")

        # Determine tool type
        if isinstance(tool, HostedMCPTool):
            tool_type = "mcp"
        elif isinstance(tool, FunctionTool):
            tool_type = "function"
        elif "handoff" in tool_name.lower() or "transfer" in tool_name.lower():
            tool_type = "handoff"
        else:
            tool_type = "unknown"

        self._current_tool_start[tool_name] = time.time()

        logger.debug(
            "Tool started: name=%s, type=%s, agent=%s",
            tool_name,
            tool_type,
            agent.name,
        )

    async def on_tool_end(
        self,
        context: RunContextWrapper[dict[str, Any]],
        agent: Agent[Any],
        tool: Tool,
        result: object,
    ):
        """Called when a tool invocation completes."""
        tool_name = getattr(tool, "name", "unknown_tool")

        start_time = self._current_tool_start.pop(tool_name, time.time())
        duration_ms = (time.time() - start_time) * 1000

        # Determine tool type
        if isinstance(tool, HostedMCPTool):
            tool_type = "mcp"
        elif isinstance(tool, FunctionTool):
            tool_type = "function"
        elif "handoff" in tool_name.lower() or "transfer" in tool_name.lower():
            tool_type = "handoff"
        else:
            tool_type = "unknown"

        # Check for errors in result
        success = True
        error = None
        if isinstance(result, str) and "error" in result.lower():
            success = False
            error = result[:200]
        elif result is None:
            success = False
            error = "Tool returned None"

        record = ToolCallRecord(
            tool_name=tool_name,
            tool_type=tool_type,
            success=success,
            duration_ms=duration_ms,
            error=error,
        )
        self.tool_call_records.append(record)

        logger.debug(
            "Tool ended: name=%s, success=%s, duration=%.1fms",
            tool_name,
            success,
            duration_ms,
        )

    # ── Handoff Hook ──────────────────────────
    # The SDK `on_handoff` hook is intentionally not overridden. EdgeDispatch
    # escalation is driven by the `escalate_query` function tool which sets
    # `context.context["escalated"] = True`; the orchestrator inspects that
    # flag rather than relying on SDK handoff() events.

    # ── LLM Call Hooks ────────────────────────

    async def on_llm_start(
        self,
        context: RunContextWrapper[dict[str, Any]],
        agent: Agent[Any],
        system_prompt: str | None,
        input_items: list[Any],
    ):
        """Called before an LLM call."""
        logger.debug(
            "LLM call starting: agent=%s, input_items=%d",
            agent.name,
            len(input_items) if input_items else 0,
        )

    async def on_llm_end(
        self,
        context: RunContextWrapper[dict[str, Any]],
        agent: Agent[Any],
        response: ModelResponse,
    ):
        """Called after an LLM call completes."""
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

        turn_record = AgentTurnRecord(
            agent_name=agent.name,
            turn_number=self.local_turn_count + self.high_end_turn_count + 1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls_made=self.tool_call_count,
        )
        self.turn_records.append(turn_record)

        logger.debug(
            "LLM call ended: agent=%s, input=%d, output=%d tokens",
            agent.name,
            input_tokens,
            output_tokens,
        )

    # ── Utility Methods ───────────────────────

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of all tracing data for this session."""
        usage = self.get_token_usage()
        return {
            "total_tool_calls": self.tool_call_count,
            "tool_calls_by_type": self._count_by_type(),
            "local_turns": self.local_turn_count,
            "high_end_turns": self.high_end_turn_count,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "tool_call_details": [
                {
                    "name": t.tool_name,
                    "type": t.tool_type,
                    "success": t.success,
                    "duration_ms": round(t.duration_ms, 1),
                    "error": t.error,
                }
                for t in self.tool_call_records
            ],
        }

    def get_token_usage(self) -> "TokenUsage":
        """Aggregate input/output tokens across all recorded LLM turns."""
        # Local import to avoid a circular dependency at module load time.
        from server.cost import TokenUsage

        return TokenUsage(
            input_tokens=sum(r.input_tokens for r in self.turn_records),
            output_tokens=sum(r.output_tokens for r in self.turn_records),
        )

    def _count_by_type(self) -> dict[str, int]:
        """Count tool calls grouped by type."""
        counts: dict[str, int] = {}
        for record in self.tool_call_records:
            counts[record.tool_type] = counts.get(record.tool_type, 0) + 1
        return counts

    def reset(self):
        """Reset all counters and records for a new session."""
        self.tool_call_count = 0
        self.local_turn_count = 0
        self.high_end_turn_count = 0
        self.tool_call_records.clear()
        self.turn_records.clear()
        self._current_tool_start.clear()


# ──────────────────────────────────────────────
# Arize Phoenix / OpenInference Integration
# ──────────────────────────────────────────────

class ArizeEvaluator:
    """
    Integration with Arize AI Phoenix for observability and evaluation.

    Handles:
      - Instrumenting the OpenAI client for OpenInference tracing
      - Logging evaluation data for tool selection and answer correctness
      - Sending spans and metrics to the Arize Phoenix collector
    
    Uses the OpenInference semantic conventions for LLM observability.
    """
    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        project_name: str | None = None,
    ):
        self.endpoint = endpoint or ARIZE_PHOENIX_ENDPOINT
        self.api_key = api_key or ARIZE_API_KEY
        self.project_name = project_name or ARIZE_PROJECT_NAME
        self._evaluation_records: list[dict[str, Any]] = []
        self._phoenix_launched = False

    def instrument_openai(self):
        """
        Set up OpenInference instrumentation for the OpenAI client.

        This injects tracing into the OpenAI API calls so that every model
        invocation is captured as a span in Phoenix.
        """
        try:
            from openinference.instrumentation.openai import OpenAIInstrumentor

            instrumentor = OpenAIInstrumentor()
            # Instrument only if not already instrumented
            if not getattr(instrumentor, "_is_instrumented", False):
                instrumentor.instrument()
                logger.info("OpenAI client instrumented with OpenInference")

        except ImportError:
            logger.warning(
                "openinference-instrumentation-openai not available. "
                "Install with: pip install openinference-instrumentation-openai"
            )
        except Exception as e:
            logger.warning("Failed to instrument OpenAI client: %s", e)

    def launch_phoenix(self):
        """
        Launch the Arize Phoenix UI server for trace inspection.

        Phoenix runs as a local web server (in a background thread) for
        debugging and evaluation. In production, traces would be sent to the
        Arize cloud platform. The endpoint is parsed into host/port.
        """
        if self._phoenix_launched:
            return

        try:
            import phoenix as px
            from urllib.parse import urlparse

            parsed = urlparse(self.endpoint)
            host = parsed.hostname or "localhost"
            port = parsed.port or 6006

            session = px.launch_app(host=host, port=port, run_in_thread=True)
            self._phoenix_launched = True
            logger.info(
                "Arize Phoenix launched at http://%s:%d (project: %s)",
                host,
                port,
                self.project_name,
            )
            return session
        except ImportError:
            logger.warning(
                "arize-phoenix not available. "
                "Install with: pip install arize-phoenix"
            )
        except Exception as e:
            logger.warning("Failed to launch Phoenix: %s", e)
        return None

    def log_evaluation(
        self,
        query: str,
        answer: str,
        was_escalated: bool,
        tool_count: int,
        threshold: int,
        trace_data: dict[str, Any],
    ):
        """
        Log evaluation data for a single query execution.

        Records:
          - Tool selection decision (which tools were chosen)
          - Routing decision (local vs. escalated)
          - Answer correctness metadata (for later rubric scoring)

        Per the thesis Section 4.6/4.7 evaluation framework.
        """
        record = {
            "timestamp": time.time(),
            "query": query[:500],
            "answer": answer[:500],
            "was_escalated": was_escalated,
            "tool_count": tool_count,
            "threshold": threshold,
            "route_decision": "escalated" if was_escalated else "local",
            "trace_data": trace_data,
            "project": self.project_name,
        }
        self._evaluation_records.append(record)

        # Log as structured event for Phoenix
        logger.info(
            "Evaluation logged: route=%s, tools=%d/%d, query='%s...'",
            record["route_decision"],
            tool_count,
            threshold,
            query[:60],
        )

    def evaluate_tool_selection(
        self,
        predicted_tools: list[str],
        ground_truth_tools: list[str],
    ) -> dict[str, float]:
        """
        Evaluate tool selection quality using Tool F1 metric (per Section 4.6).

        Args:
            predicted_tools: Tools selected by the system
            ground_truth_tools: Correct tools for the query

        Returns:
            Dict with precision, recall, f1, and invalid_tool_rate
        """
        predicted_set = set(predicted_tools)
        ground_set = set(ground_truth_tools)

        if not predicted_set:
            return {
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "invalid_tool_rate": 0.0,
            }

        intersection = predicted_set & ground_set
        invalid = predicted_set - ground_set

        precision = len(intersection) / len(predicted_set) if predicted_set else 0.0
        recall = len(intersection) / len(ground_set) if ground_set else 1.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        invalid_rate = len(invalid) / len(predicted_set) if predicted_set else 0.0

        metrics = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "invalid_tool_rate": round(invalid_rate, 4),
            "predicted_count": len(predicted_set),
            "ground_truth_count": len(ground_set),
            "correct_count": len(intersection),
            "invalid_tools": list(invalid),
        }

        logger.info(
            "Tool selection eval: F1=%.3f, precision=%.3f, recall=%.3f, invalid=%.3f",
            f1,
            precision,
            recall,
            invalid_rate,
        )

        return metrics

    def evaluate_answer_correctness(
        self,
        answer: str,
        evidence: list[dict[str, Any]],
        query: str,
    ) -> dict[str, Any]:
        """
        Evaluate answer correctness against the 6-point rubric (Section 4.7).

        Rubric dimensions:
          - D1: Factual accuracy (0-2)
          - D2: Evidence traceability (0-2)
          - D3: Query resolution (0-1)
          - D4: Constraint discipline (0-1)

        This method provides a structural framework. In the full implementation
        (Thesis B), a rubric-scoring model or human reviewers would populate
        the actual scores.
        """
        # Heuristic-based scoring for the prototype
        scores = {
            "d1_factual_accuracy": self._score_factual_accuracy(answer, evidence),
            "d2_evidence_traceability": self._score_evidence_traceability(answer, evidence),
            "d3_query_resolution": self._score_query_resolution(answer, query),
            "d4_constraint_discipline": self._score_constraint_discipline(answer),
        }

        total = sum(scores.values())
        max_score = 6.0

        result = {
            **scores,
            "total": total,
            "max_score": max_score,
            "normalized": round(total / max_score, 4),
            "passes_poc_threshold": total >= 2.5,  # Within 0.5 of 3.0 baseline
        }

        return result

    @staticmethod
    def _score_factual_accuracy(answer: str, evidence: list[dict[str, Any]]) -> int:
        """
        Score factual accuracy (D1: 0-2).

        Heuristic: check if answer references data from evidence.
        Full eval requires human review or a judge LLM.
        """
        if not evidence:
            return 0

        answer_lower = answer.lower()
        evidence_terms: set[str] = set()
        for item in evidence:
            result_str = str(item.get("result", "")).lower()
            for word in result_str.split():
                if len(word) > 3:
                    evidence_terms.add(word)

        matches = sum(1 for t in evidence_terms if t in answer_lower)

        if matches >= 3:
            return 2  # Strong evidence grounding
        elif matches >= 1:
            return 1  # Partial grounding
        return 0  # No grounding detected

    @staticmethod
    def _score_evidence_traceability(answer: str, evidence: list[dict[str, Any]]) -> int:
        """
        Score evidence traceability (D2: 0-2).

        Heuristic: detect explicit source references in the answer.
        """
        answer_lower = answer.lower()
        source_indicators = [
            "according to", "based on", "from the", "as shown in",
            "the document", "the database", "the policy", "the record",
        ]

        indicators_found = sum(1 for ind in source_indicators if ind in answer_lower)

        if indicators_found >= 2:
            return 2
        elif indicators_found >= 1:
            return 1
        return 0

    @staticmethod
    def _score_query_resolution(answer: str, query: str) -> int:
        """
        Score query resolution (D3: 0-1).

        Heuristic: check if answer is substantial and addresses query topics.
        """
        if len(answer.strip()) < 20:
            return 0

        query_terms = {w.lower() for w in query.split() if len(w) > 3}
        answer_terms = {w.lower() for w in answer.split() if len(w) > 3}

        overlap = query_terms & answer_terms

        if len(overlap) >= 2 or len(answer) > 200:
            return 1
        return 0

    @staticmethod
    def _score_constraint_discipline(answer: str) -> int:
        """
        Score constraint discipline (D4: 0-1).

        Heuristic: penalize hallucination markers and fabrication signals.
        """
        answer_lower = answer.lower()
        fabrication_markers = [
            "i made up", "i fabricated", "i don't know",
        ]
        constraint_markers = [
            "insufficient evidence", "no evidence", "could not find",
            "not specified", "not mentioned", "unable to determine",
        ]

        if any(m in answer_lower for m in fabrication_markers):
            return 0

        # Honest constraint acknowledgment is positive
        if any(m in answer_lower for m in constraint_markers):
            return 1

        return 1  # Default for prototype — no obvious violations

    def flush_evaluations(self) -> list[dict[str, Any]]:
        """Return and clear all evaluation records."""
        records = self._evaluation_records.copy()
        self._evaluation_records.clear()
        return records

    def get_evaluation_summary(self) -> dict[str, Any]:
        """Get aggregate statistics across all evaluations in this session."""
        if not self._evaluation_records:
            return {"total_evaluations": 0}

        escalated = sum(1 for r in self._evaluation_records if r["was_escalated"])
        local = len(self._evaluation_records) - escalated
        avg_tools = sum(r["tool_count"] for r in self._evaluation_records) / len(self._evaluation_records)

        return {
            "total_evaluations": len(self._evaluation_records),
            "escalated_count": escalated,
            "local_count": local,
            "escalation_rate": round(escalated / len(self._evaluation_records), 4),
            "avg_tool_count": round(avg_tools, 2),
            "threshold": self._evaluation_records[0]["threshold"] if self._evaluation_records else 0,
        }
