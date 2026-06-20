"""
EdgeDispatch - API Models

Pydantic schemas for FastAPI request/response validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Chat Models
# ──────────────────────────────────────────────

class ChatMessage(BaseModel):
    """A single message in a conversation."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str  # "user" or "assistant"
    content: str
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())


class ChatRequest(BaseModel):
    """Request body for sending a chat message."""
    query: str
    conversation_id: str | None = None
    tool_threshold: int | None = None


class Conversation(BaseModel):
    """A full conversation with messages."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "New conversation"
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: float = Field(default_factory=lambda: datetime.now().timestamp())
    updated_at: float = Field(default_factory=lambda: datetime.now().timestamp())


# ──────────────────────────────────────────────
# Streaming Event Models
# ──────────────────────────────────────────────

class StreamEvent(BaseModel):
    """An SSE event emitted during chat processing."""
    type: str  # "token", "status", "error", "done", "metadata"
    data: str
    metadata: dict[str, Any] | None = None


# ──────────────────────────────────────────────
# Settings Models
# ──────────────────────────────────────────────

class SettingsRequest(BaseModel):
    """Request body for updating settings (partial update)."""
    tool_threshold: int | None = None
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None


class SettingsResponse(BaseModel):
    """Current system configuration."""
    tool_threshold: int
    price_input_per_mtok: float
    price_output_per_mtok: float
    local_model: str
    high_end_model: str
    mcp_server_count: int
    arize_endpoint: str


# ──────────────────────────────────────────────
# Health Model
# ──────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    version: str = "0.1.0"
