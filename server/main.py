"""
EdgeDispatch  - FastAPI Application Entry Point

Serves the EdgeDispatch hybrid LLM orchestration backend with:
  - SSE streaming chat endpoint
  - Conversation CRUD
  - Settings management (threshold + pricing)
  - Health check
  - CORS for frontend
  - MCP server lifecycle (stdio stubs for the prototype source environment)

Usage:
    uv run uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.mcp import MCPServerStdio, MCPServerStdioParams

from server.config import LOG_LEVEL, MCP_SERVER_CONFIGS
from server.agent_definition import EdgeDispatchOrchestrator
from server.routes import chat, settings

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("EdgeDispatch")

# Global orchestrator instance
_orchestrator: EdgeDispatchOrchestrator | None = None


def _build_mcp_servers() -> list[MCPServerStdio]:
    """Build MCPServerStdio instances from the prototype config.

    Each entry in MCP_SERVER_CONFIGS launches a stub MCP server as a stdio
    subprocess (thesis Section 4.2 source archetypes: document store,
    relational DB, policy wiki). The subprocess cwd is the project root so
    `python -m mcp_server_*` resolves the local packages.
    """
    project_root = os.getcwd()
    servers: list[MCPServerStdio] = []
    for cfg in MCP_SERVER_CONFIGS:
        params = MCPServerStdioParams(
            command=cfg["command"],
            args=cfg.get("args", []),
            cwd=project_root,
        )
        servers.append(
            MCPServerStdio(
                params=params,
                name=cfg["name"],
                cache_tools_list=True,
            )
        )
    return servers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and clean up the orchestrator + MCP servers."""
    global _orchestrator

    logger.info("Starting EdgeDispatch server...")

    mcp_servers = _build_mcp_servers()
    logger.info("Built %d MCP stdio servers: %s", len(mcp_servers), [s.name for s in mcp_servers])

    # Connect each MCP server before handing them to the orchestrator.
    for server in mcp_servers:
        try:
            await server.connect()
            logger.info("Connected MCP server: %s", server.name)
        except Exception as e:
            logger.warning("Failed to connect MCP server %s: %s (continuing)", server.name, e)

    _orchestrator = EdgeDispatchOrchestrator(
        mcp_servers=mcp_servers,
        tool_threshold=2,
    )

    # Inject orchestrator into chat routes
    chat.set_orchestrator(_orchestrator)

    logger.info("EdgeDispatch orchestrator ready")

    yield

    # Cleanup
    if _orchestrator:
        await _orchestrator.close()
    for server in mcp_servers:
        try:
            await server.cleanup()
        except BaseException as e:  # best-effort teardown; don't propagate CancelledError
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            logger.warning("MCP server cleanup error (%s): %s", server.name, e)
    logger.info("EdgeDispatch server shut down")


app = FastAPI(
    title="EdgeDispatch",
    description="Hybrid LLM Orchestration for Local-First AI Workflows",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(chat.router)
app.include_router(settings.router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "orchestrator_ready": _orchestrator is not None,
    }
