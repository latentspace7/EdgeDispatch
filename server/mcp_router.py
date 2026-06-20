"""
EdgeDispatch - MCP Router Module

Handles the logic for:
  - Tool counting from compact manifests
  - Threshold-based routing decisions (local vs. escalation)
  - Manifest and sandbox environment creation
  - Structured handoff document generation

Based on the EdgeDispatch thesis architecture: the local SLM evaluates whether
a query can be resolved locally or requires a conditional handoff to the cloud.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from server.config import DEFAULT_TOOL_THRESHOLD

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────

@dataclass
class ToolManifestEntry:
    """A single entry in the compact tool manifest.

    Based on the EdgeDispatch thesis Section 2.2: each entry lists the tool's
    identifier, type, content description, and question affinities.
    """
    name: str
    tool_type: str
    description: str
    question_affinities: list[str] = field(default_factory=list)
    source_archetype: str = ""  # e.g. "document_store", "relational_db", "policy_wiki"
    mcp_server: str = ""


@dataclass
class HandoffDocument:
    """Structured handoff document passed from local to cloud agent.

    Per Section 2.2 of the thesis: H = {Q, Ŝ(Q), E} where:
      - Q: User query
      - Ŝ(Q): Selected tool subset
      - E: Retrieved evidence
    """
    query: str
    selected_tools: list[str]
    rationale: str
    evidence: list[dict[str, Any]]
    tool_threshold: int = DEFAULT_TOOL_THRESHOLD

    def to_compact_prompt(self) -> str:
        """Convert the handoff document to a concise prompt for the cloud model."""
        evidence_str = json.dumps(self.evidence, indent=2)
        tools_str = ", ".join(self.selected_tools)

        lines = [
            "## Handoff from EdgeDispatch Local Agent",
            "",
            f"**Query:** {self.query}",
            "",
            f"**Tools Invoked:** {tools_str}",
            "",
            f"**Rationale:** {self.rationale}",
            "",
            "**Evidence:**",
            evidence_str,
            "",
            "Synthesize a final answer using only the above evidence.",
        ]
        return "\n".join(lines)

    def estimate_tokens(self) -> int:
        """Rough token estimate for the handoff document (characters / 4)."""
        content = self.to_compact_prompt()
        return len(content) // 4


# ──────────────────────────────────────────────
# Query Analysis and Tool Counting
# ──────────────────────────────────────────────

# Keywords that suggest specific tool categories
ARCHETYPE_KEYWORDS: dict[str, list[str]] = {
    "document_store": [
        "document", "report", "pdf", "paper", "file", "read", "page", "chapter",
        "section", "specification", "manual", "guide", "record", "memo",
    ],
    "relational_db": [
        "status", "approval", "id", "record", "database", "lookup", "query",
        "select", "update", "insert", "row", "table", "column", "field",
        "req-", "ticket-", "case-",
    ],
    "policy_wiki": [
        "policy", "wiki", "compliance", "regulation", "rule", "guideline",
        "standard", "procedure", "protocol", "requirement",
    ],
}


def _count_tool_mentions(query: str, manifest: list[ToolManifestEntry]) -> dict[str, int]:
    """Count how many keywords from each tool's affinities appear in the query."""
    query_lower = query.lower()
    scores: dict[str, int] = {}

    for entry in manifest:
        score = 0
        for affinity in entry.question_affinities:
            if affinity.lower() in query_lower:
                score += 1
        # Also check archetype keywords
        keywords = ARCHETYPE_KEYWORDS.get(entry.source_archetype, [])
        for kw in keywords:
            if kw.lower() in query_lower:
                score += 1

        if score > 0:
            scores[entry.name] = score

    return scores


def _estimate_distinct_tools(query: str, manifest: list[ToolManifestEntry]) -> int:
    """
    Heuristically estimate how many distinct tool types a query needs.
    
    Uses:
      1. Keyword matching against tool affinities and archetype keywords
      2. Query complexity signals (conjunctions, multiple question marks, etc.)
      3. Presence of identifiers suggesting structured lookups
    """
    scores = _count_tool_mentions(query, manifest)
    distinct_sources = len(set(
        e.source_archetype for e in manifest
        if e.name in scores and scores[e.name] > 0
    ))

    # Count multi-part query signals
    query_lower = query.lower()
    multi_signals = sum([
        query_lower.count(" and "),
        query_lower.count("?"),
        1 if re.search(r"what.*(?:and|,).*(?:what|how|which|why)", query_lower) else 0,
    ])

    # Estimate: max of (keyword-matched tools, distinct source types, 1)
    estimated = max(len(scores), distinct_sources, 1)

    # Add bonus for multi-part questions
    if multi_signals >= 2:
        estimated = max(estimated, 2)
    if multi_signals >= 3:
        estimated = max(estimated, 3)

    return min(estimated, len(manifest))


# ──────────────────────────────────────────────
# MCP Router
# ──────────────────────────────────────────────

class MCPRouter:
    """
    Core router implementing the EdgeDispatch tool-threshold logic.
    
    Responsibilities:
      - Build and maintain the compact tool manifest
      - Analyze queries to estimate tool requirements
      - Compare tool count against user-defined threshold
      - Decide: resolve locally or escalate to high-end model
    
    Decision rule (per Section 2.2):
      If N >= tool_threshold → escalate (D=1)
      If N < tool_threshold  → resolve locally (D=0)
    """
    def __init__(self, threshold: int = DEFAULT_TOOL_THRESHOLD):
        self.threshold = threshold
        self.manifest: list[ToolManifestEntry] = []
        self._decision_history: list[dict[str, Any]] = []

    def build_manifest_from_servers(self, mcp_servers: list[Any]) -> str:
        """
        Build a compact tool manifest from connected MCP servers.

        This creates a text representation of available tools that the local
        SLM can read to make dispatch decisions without seeing full schemas.

        Args:
            mcp_servers: List of MCPServer instances

        Returns:
            String representation of the compact tool manifest
        """
        self.manifest = []

        for server in mcp_servers:
            server_name = getattr(server, "name", "unknown")
            # Infer source archetype from server name
            source = self._infer_archetype(server_name)

            # Build entries — in practice, this would introspect the actual
            # tools from each MCP server, but for the prototype we create
            # representative entries.
            if source == "document_store":
                self._add_manifest_entry(
                    "search_documents", "search",
                    "Search across PDFs, DOCX files, and technical reports",
                    ["document", "report", "pdf", "paper", "file", "find", "search"],
                    source, server_name,
                )
                self._add_manifest_entry(
                    "get_document", "retrieval",
                    "Retrieve a specific document by ID or title",
                    ["get", "retrieve", "fetch", "read", "open"],
                    source, server_name,
                )

            elif source == "relational_db":
                self._add_manifest_entry(
                    "db_lookup", "query",
                    "Run SQL queries against approval records and structured metadata",
                    ["status", "approval", "id", "req-", "ticket", "case", "lookup", "record"],
                    source, server_name,
                )
                self._add_manifest_entry(
                    "db_search", "search",
                    "Full-text search across database fields",
                    ["find", "search", "query", "filter", "list"],
                    source, server_name,
                )

            elif source == "policy_wiki":
                self._add_manifest_entry(
                    "wiki_search", "search",
                    "Search policy wiki pages by keyword or topic",
                    ["policy", "wiki", "compliance", "rule", "guideline", "regulation"],
                    source, server_name,
                )
                self._add_manifest_entry(
                    "wiki_get_page", "retrieval",
                    "Retrieve a specific wiki page by title",
                    ["page", "read", "get", "retrieve", "view"],
                    source, server_name,
                )

        # Build the manifest string
        return json.dumps(
            [
                {
                    "name": e.name,
                    "type": e.tool_type,
                    "description": e.description,
                    "affinities": e.question_affinities,
                    "source": e.source_archetype,
                }
                for e in self.manifest
            ],
            indent=2,
        )

    def _add_manifest_entry(
        self,
        name: str,
        tool_type: str,
        description: str,
        affinities: list[str],
        source: str,
        server: str,
    ):
        """Add an entry to the manifest."""
        self.manifest.append(ToolManifestEntry(
            name=name,
            tool_type=tool_type,
            description=description,
            question_affinities=affinities,
            source_archetype=source,
            mcp_server=server,
        ))

    @staticmethod
    def _infer_archetype(server_name: str) -> str:
        """Infer the source archetype from a server name."""
        name_lower = server_name.lower()
        if any(kw in name_lower for kw in ["doc", "pdf", "file"]):
            return "document_store"
        if any(kw in name_lower for kw in ["db", "sql", "database", "table"]):
            return "relational_db"
        if any(kw in name_lower for kw in ["wiki", "policy", "knowledge"]):
            return "policy_wiki"
        return "document_store"  # default

    def analyze_query(self, query: str, manifest_str: str | None = None) -> dict[str, Any]:
        """
        Analyze a query to determine if it should be resolved locally or escalated.

        Args:
            query: The user query string
            manifest_str: Optional manifest override (uses stored manifest if None)

        Returns:
            Dict with:
              - estimated_tool_count: Number of distinct tools estimated
              - route_decision: 'local' or 'escalated'
              - required_tools: List of tool names identified
              - rationale: Reason for the decision
        """
        estimated = _estimate_distinct_tools(query, self.manifest)
        required_tools = [
            name for name, score in _count_tool_mentions(query, self.manifest).items()
            if score > 0
        ]

        if estimated >= self.threshold:
            decision = "escalated"
            rationale = (
                f"Estimated {estimated} tools needed, which meets or exceeds the "
                f"threshold of {self.threshold}. Escalating to high-end cloud model."
            )
        else:
            decision = "local"
            rationale = (
                f"Estimated {estimated} tools needed, which is below the threshold "
                f"of {self.threshold}. Resolving locally."
            )

        result = {
            "estimated_tool_count": estimated,
            "route_decision": decision,
            "required_tools": required_tools,
            "rationale": rationale,
            "threshold": self.threshold,
        }

        self._decision_history.append({
            "query": query[:200],
            "result": result,
        })

        logger.debug("Router decision: %s (estimated=%d, threshold=%d)", decision, estimated, self.threshold)
        return result

    def create_manifest_for_escalation(
        self, required_tools: list[str]
    ) -> dict[str, Any]:
        """
        Create a sandbox/environment manifest for escalation.

        Per the thesis, a Manifest defines the sandbox/environment boundaries
        when escalating. This contains only the tool schemas actually needed.
        """
        filtered_entries = [
            e for e in self.manifest if e.name in required_tools
        ]

        manifest = {
            "sandbox_name": "EdgeDispatch Escalation Sandbox",
            "boundary_tools": [
                {
                    "name": e.name,
                    "type": e.tool_type,
                    "description": e.description,
                    "source": e.source_archetype,
                }
                for e in filtered_entries
            ],
            "threshold": self.threshold,
            "total_available_tools": len(self.manifest),
            "tools_in_scope": len(filtered_entries),
        }

        logger.info("Created escalation manifest: %d tools in scope", len(filtered_entries))
        return manifest

    def create_handoff_document(
        self,
        query: str,
        selected_tools: list[str],
        evidence: list[dict[str, Any]],
        rationale: str = "",
    ) -> HandoffDocument:
        """
        Generate a structured handoff document H = {Q, Ŝ(Q), E}.

        Per Section 4.5 Algorithm 1, this is the document compiled by the local
        SLM and transmitted to the cloud model when D=1.
        """
        if not rationale:
            rationale = (
                f"Query required {len(selected_tools)} tools, "
                f"exceeding the threshold of {self.threshold}. "
                f"Evidence collected from: {', '.join(selected_tools)}."
            )

        doc = HandoffDocument(
            query=query,
            selected_tools=selected_tools,
            rationale=rationale,
            evidence=evidence,
            tool_threshold=self.threshold,
        )

        estimated_tokens = doc.estimate_tokens()
        logger.info(
            "Handoff document created: %d tools, ~%d tokens",
            len(selected_tools),
            estimated_tokens,
        )
        return doc

    def get_decision_history(self) -> list[dict[str, Any]]:
        """Return the history of routing decisions for observability."""
        return self._decision_history

    def reset_history(self):
        """Clear the decision history."""
        self._decision_history.clear()
