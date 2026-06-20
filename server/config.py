"""
EdgeDispatch - Configuration Module

Shared configuration constants for the EdgeDispatch system.
Override these via environment variables or by modifying this file directly.
"""

import os


# ──────────────────────────────────────────────
# Local Model (llama.cpp via OpenAI-compatible API)
# ──────────────────────────────────────────────

LOCAL_MODEL_NAME = os.environ.get("EDGE_LOCAL_MODEL_NAME", "local-model")
LOCAL_MODEL_BASE_URL = os.environ.get(
    "EDGE_LOCAL_BASE_URL", "http://localhost:8080/v1"
)
LOCAL_MODEL_API_KEY = os.environ.get("EDGE_LOCAL_API_KEY", "not-needed")

# ──────────────────────────────────────────────
# High-End Model (OpenAI / cloud frontier)
# ──────────────────────────────────────────────

HIGH_END_MODEL_NAME = os.environ.get(
    "EDGE_HIGH_END_MODEL_NAME", "gpt-4o"
)
HIGH_END_MODEL_API_KEY = os.environ.get(
    "EDGE_HIGH_END_API_KEY", os.environ.get("OPENAI_API_KEY", "")
)
HIGH_END_MODEL_BASE_URL = os.environ.get(
    "EDGE_HIGH_END_BASE_URL", "https://api.openai.com/v1"
)

# ──────────────────────────────────────────────
# Routing & Thresholds
# ──────────────────────────────────────────────

DEFAULT_TOOL_THRESHOLD = int(
    os.environ.get("EDGE_TOOL_THRESHOLD", "2")
)

# ──────────────────────────────────────────────
# Pricing (per-million-token rates, USD)
# ──────────────────────────────────────────────
# User-configurable via the UI; these are the prototype defaults. The thesis
# Table 2.1 uses a representative cloud input rate of $5.00/M input tokens.
# Output pricing is included because real APIs bill input and output separately.
DEFAULT_PRICE_INPUT_PER_MTOK = float(
    os.environ.get("EDGE_PRICE_INPUT_PER_MTOK", "5.0")
)
DEFAULT_PRICE_OUTPUT_PER_MTOK = float(
    os.environ.get("EDGE_PRICE_OUTPUT_PER_MTOK", "15.0")
)

# ──────────────────────────────────────────────
# Agent Execution Limits
# ──────────────────────────────────────────────

MAX_TURNS_LOCAL = int(os.environ.get("EDGE_MAX_TURNS_LOCAL", "10"))
MAX_TURNS_HIGH_END = int(os.environ.get("EDGE_MAX_TURNS_HIGH_END", "5"))

# ──────────────────────────────────────────────
# Model Temperature
# ──────────────────────────────────────────────

TEMPERATURE_LOCAL = float(os.environ.get("EDGE_TEMPERATURE_LOCAL", "0.0"))
TEMPERATURE_HIGH_END = float(os.environ.get("EDGE_TEMPERATURE_HIGH_END", "0.3"))

# ──────────────────────────────────────────────
# MCP Server Configuration
# ──────────────────────────────────────────────

# MCP servers are configured here for the prototype.
# In production, these would be dynamically discovered or configured externally.
MCP_SERVER_CONFIGS = [
    {
        "name": "document_store",
        "command": "python",
        "args": ["-m", "mcp_server_docs"],
        "description": "Document store: PDFs, DOCX, technical reports",
    },
    {
        "name": "relational_db",
        "command": "python",
        "args": ["-m", "mcp_server_db"],
        "description": "Relational database: approval records and metadata",
    },
    {
        "name": "policy_wiki",
        "command": "python",
        "args": ["-m", "mcp_server_wiki"],
        "description": "Policy wiki: compliance and procedure pages",
    },
]

# ──────────────────────────────────────────────
# Arize Phoenix Observability
# ──────────────────────────────────────────────

ARIZE_PHOENIX_ENDPOINT = os.environ.get(
    "ARIZE_PHOENIX_ENDPOINT", "http://localhost:6006"
)
ARIZE_API_KEY = os.environ.get("ARIZE_API_KEY", "")
ARIZE_PROJECT_NAME = os.environ.get("ARIZE_PROJECT_NAME", "EdgeDispatch")

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

LOG_LEVEL = os.environ.get("EDGE_LOG_LEVEL", "INFO")
