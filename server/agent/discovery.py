from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolBinding:
    server: Any
    name: str
    description: str
    parameters: dict[str, Any]
    requires_approval: bool

    def openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(self.parameters),
            },
        }


@dataclass(frozen=True)
class Catalogue:
    bindings: tuple[ToolBinding, ...]
    unavailable_servers: tuple[str, ...]

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [binding.openai_tool() for binding in self.bindings]


async def discover_tools(
    servers: list[Any],
    *,
    unavailable: tuple[str, ...] = (),
    allowlist: set[tuple[str, str]] | None = None,
) -> Catalogue:
    bindings: list[ToolBinding] = []
    names: set[str] = set()
    missing = set(unavailable)
    for server in servers:
        try:
            tools = await server.list_tools()
        except Exception:
            missing.add(server.name)
            continue
        for tool in tools:
            if allowlist is not None and (server.name, tool.name) not in allowlist:
                continue
            if tool.name in names:
                raise ValueError(f"Ambiguous MCP tool name: {tool.name}")
            names.add(tool.name)
            parameters = copy.deepcopy(tool.inputSchema)
            if parameters.get("type") != "object":
                raise ValueError(f"MCP tool requires an object schema: {tool.name}")
            annotations = getattr(tool, "annotations", None)
            read_only = (
                annotations is not None
                and getattr(annotations, "readOnlyHint", None) is True
            )
            bindings.append(
                ToolBinding(
                    server, tool.name, tool.description or "", parameters, not read_only
                )
            )
    return Catalogue(tuple(bindings), tuple(sorted(missing)))
