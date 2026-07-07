"""
EdgeDispatch - MCP Router Module

Handles the logic for:
  - Compact MCP tool manifests with schemas
  - Manifest and sandbox environment creation
  - Structured handoff document generation

Based on the EdgeDispatch thesis architecture: the local SLM evaluates whether
a query can be resolved locally or requires a conditional handoff to the cloud.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from server.config import DEFAULT_TOOL_THRESHOLD

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────

@dataclass
class ToolManifestEntry:
    """A single entry in the compact tool manifest.

    Based on the EdgeDispatch thesis Section 2.2: each entry lists the tool's
    identifier, type, content description, source, and compact schema.
    """
    name: str
    tool_type: str
    description: str
    source_archetype: str = ""  # e.g. "document_store", "relational_db", "policy_wiki"
    mcp_server: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "string"})

    def handoff_schema(self) -> dict[str, Any]:
        """Compact schema included only for tools actually invoked."""
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source_archetype,
            "mcp_server": self.mcp_server,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


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

# ──────────────────────────────────────────────
# MCP Router
# ──────────────────────────────────────────────

class MCPRouter:
    """
    Manifest and handoff helper for the EdgeDispatch tool-threshold logic.
    
    Responsibilities:
      - Build and maintain the compact tool manifest
      - Keep per-tool schema metadata for handoff evidence
      - Build escalation manifests from the model's actual tool choices
    
    Dispatch is model-driven. The local agent decides which tools to invoke; the
    orchestrator compares actual MCP calls against the UI-configured threshold
    after the local run.
    """
    def __init__(self, threshold: int = DEFAULT_TOOL_THRESHOLD):
        self.threshold = threshold
        self.manifest: list[ToolManifestEntry] = []

    def build_manifest_from_servers(self, mcp_servers: list[Any]) -> str:
        """
        Build a compact tool manifest from connected MCP servers.

        This creates a text representation of available tools that the local
        SLM can read to choose tools, including compact input/output schemas.

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

            # Build entries. In practice, this would introspect the actual
            # tools from each MCP server, but for the prototype we create
            # representative entries.
            if source == "document_store":
                self._add_manifest_entry(
                    "search_documents", "search",
                    "Search employee leave balances, leave statements, and HR documents",
                    source, server_name,
                    self._single_string_param_schema(
                        "query",
                        "Free-text search query over HR leave documents.",
                    ),
                )
                self._add_manifest_entry(
                    "get_document", "retrieval",
                    "Retrieve a specific HR document or employee leave statement by ID",
                    source, server_name,
                    self._single_string_param_schema(
                        "doc_id",
                        "Document identifier, for example LEAVE-EMP-1001.",
                    ),
                )

            elif source == "relational_db":
                self._add_manifest_entry(
                    "db_lookup", "query",
                    "Look up employee records and structured HR metadata",
                    source, server_name,
                    self._single_string_param_schema(
                        "record_id",
                        "Employee identifier, for example EMP-1001.",
                    ),
                )
                self._add_manifest_entry(
                    "db_search", "search",
                    "Full-text search across employee database fields",
                    source, server_name,
                    self._single_string_param_schema(
                        "query",
                        "Search terms such as employee name, department, role, gender, or manager.",
                    ),
                )

            elif source == "policy_wiki":
                self._add_manifest_entry(
                    "wiki_search", "search",
                    "Search HR policy wiki pages by keyword or topic",
                    source, server_name,
                    self._single_string_param_schema(
                        "query",
                        "Search terms for HR policy wiki pages.",
                    ),
                )
                self._add_manifest_entry(
                    "wiki_get_page", "retrieval",
                    "Retrieve a specific HR policy wiki page by title",
                    source, server_name,
                    self._single_string_param_schema(
                        "title",
                        "Policy wiki page slug, for example annual-leave-policy.",
                    ),
                )

        # Build the manifest string
        return json.dumps(
            [
                {
                    "name": e.name,
                    "type": e.tool_type,
                    "description": e.description,
                    "source": e.source_archetype,
                    "schema": e.handoff_schema(),
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
        source: str,
        server: str,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ):
        """Add an entry to the manifest."""
        self.manifest.append(ToolManifestEntry(
            name=name,
            tool_type=tool_type,
            description=description,
            source_archetype=source,
            mcp_server=server,
            input_schema=input_schema or {},
            output_schema=output_schema or {"type": "string"},
        ))

    @staticmethod
    def _single_string_param_schema(name: str, description: str) -> dict[str, Any]:
        """Build the compact JSON schema used by the stub single-argument tools."""
        return {
            "type": "object",
            "properties": {
                name: {
                    "type": "string",
                    "description": description,
                },
            },
            "required": [name],
            "additionalProperties": False,
        }

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

    def get_tool_schema(self, tool_name: str) -> dict[str, Any] | None:
        """Return compact schema metadata for a tool by name."""
        entry = next((e for e in self.manifest if e.name == tool_name), None)
        return entry.handoff_schema() if entry else None

    def default_tool_reason(self, tool_name: str, query: str) -> str:
        """Fallback reason when the model did not provide one explicitly."""
        entry = next((e for e in self.manifest if e.name == tool_name), None)
        if not entry:
            return f"The local model invoked `{tool_name}` while answering the query."
        return (
            f"The local model invoked `{tool_name}` because its role is: "
            f"{entry.description}. Query: {query}"
        )

    def create_manifest_for_escalation(
        self, selected_tools: list[str]
    ) -> dict[str, Any]:
        """
        Create a sandbox/environment manifest for escalation.

        Per the thesis, a Manifest defines the sandbox/environment boundaries
        when escalating. This contains only the tool schemas actually needed.
        """
        filtered_entries = [
            e for e in self.manifest if e.name in selected_tools
        ]

        manifest = {
            "sandbox_name": "EdgeDispatch Escalation Sandbox",
            "boundary_tools": [
                {
                    "name": e.name,
                    "type": e.tool_type,
                    "description": e.description,
                    "source": e.source_archetype,
                    "schema": e.handoff_schema(),
                }
                for e in filtered_entries
            ],
            "threshold": self.threshold,
            "total_available_tools": len(self.manifest),
            "tools_in_scope": len(filtered_entries),
        }

        logger.info("Created escalation manifest: %d tools in scope", len(filtered_entries))
        return manifest
