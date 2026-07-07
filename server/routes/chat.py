"""
EdgeDispatch - Chat Routes

Endpoints for chat streaming (SSE) and conversation management.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from server.models.schemas import (
    ChatRequest,
    Conversation,
    ChatMessage,
)
from server.routes.settings import get_current_threshold

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])
SSE_SEPARATOR = "\r\n"

# In-memory conversation store
_conversations: dict[str, Conversation] = {}

# Reference to the orchestrator (set by main.py)
_orchestrator = None


def set_orchestrator(orchestrator):
    """Set the orchestrator reference for route handlers."""
    global _orchestrator
    _orchestrator = orchestrator


def get_conversation(conv_id: str) -> Conversation:
    """Get or create a conversation."""
    if conv_id not in _conversations:
        _conversations[conv_id] = Conversation(id=conv_id)
    return _conversations[conv_id]


def store_message(conv_id: str, role: str, content: str) -> ChatMessage:
    """Store a message in a conversation."""
    conv = get_conversation(conv_id)
    msg = ChatMessage(role=role, content=content)
    conv.messages.append(msg)

    # Update title from first user message
    if role == "user" and conv.title == "New conversation":
        conv.title = content[:60] + ("..." if len(content) > 60 else "")

    conv.updated_at = datetime.now().timestamp()
    return msg


@router.post("/chat")
async def chat(body: ChatRequest):
    """
    Send a chat message and stream the response via SSE.

    Events emitted:
      - `status`: Processing status updates (tool counting, routing)
      - `token`: Content tokens from the agent response
      - `error`: Processing error
      - `done`: Completion signal with metadata
    """
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not configured")

    conv_id = body.conversation_id or str(uuid.uuid4())
    threshold = (
        body.tool_threshold
        if body.tool_threshold is not None
        else get_current_threshold()
    )

    # Store user message
    user_msg = store_message(conv_id, "user", query)

    async def event_generator():
        assistant_content = ""
        try:
            # Emit initial status
            yield {
                "event": "status",
                "data": json.dumps({
                    "type": "analyzing",
                    "message": "Running local model dispatch...",
                    "conversation_id": conv_id,
                    "message_id": user_msg.id,
                }),
            }

            # Update orchestrator threshold
            _orchestrator.set_threshold(threshold)

            yield {
                "event": "status",
                "data": json.dumps({
                    "type": "routing",
                    "message": (
                        "Local model is choosing tools. "
                        f"Escalation threshold: {threshold} actual MCP call(s)."
                    ),
                    "threshold": threshold,
                }),
            }

            result = None
            async for stream_event in _orchestrator.process_query_stream(query):
                event_type = stream_event.get("event")

                if event_type == "status":
                    yield {
                        "event": "status",
                        "data": json.dumps(stream_event.get("data", {})),
                    }
                    continue

                if event_type == "token":
                    chunk = str(stream_event.get("data", ""))
                    assistant_content += chunk
                    yield {
                        "event": "token",
                        "data": chunk,
                    }
                    continue

                if event_type == "result":
                    result = stream_event.get("result")

            if result is None:
                raise RuntimeError("Orchestrator completed without a result")

            # Emit done with metadata + cost breakdown
            done_payload: dict[str, Any] = {
                "conversation_id": conv_id,
                "was_escalated": result.was_escalated,
                "tool_count": result.tool_count,
                "threshold": result.tool_threshold,
            }
            if result.handoff_document is not None:
                done_payload["handoff"] = {
                    "query": result.handoff_document.query,
                    "selected_tools": result.handoff_document.selected_tools,
                    "rationale": result.handoff_document.rationale,
                    "evidence": result.handoff_document.evidence,
                    "tool_threshold": result.handoff_document.tool_threshold,
                    "prompt": result.handoff_document.to_compact_prompt(),
                }
            if result.cost is not None:
                done_payload["cost"] = result.cost.as_dict()
            if result.evaluation:
                done_payload["evaluation"] = result.evaluation

            yield {
                "event": "done",
                "data": json.dumps(done_payload),
            }

        except Exception as e:
            logger.exception("Chat processing error")
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": str(e),
                    "conversation_id": conv_id,
                }),
            }

        # Store assistant message
        if assistant_content:
            store_message(conv_id, "assistant", assistant_content)

    return EventSourceResponse(event_generator(), sep=SSE_SEPARATOR)


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations():
    """List all active conversations, sorted by most recent first."""
    convs = sorted(
        _conversations.values(),
        key=lambda c: c.updated_at,
        reverse=True,
    )
    return convs


@router.get("/conversations/{conv_id}", response_model=Conversation)
async def get_conversation_by_id(conv_id: str):
    """Get a specific conversation by ID."""
    conv = _conversations.get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation."""
    if conv_id in _conversations:
        del _conversations[conv_id]
        return {"status": "deleted", "id": conv_id}
    raise HTTPException(status_code=404, detail="Conversation not found")


@router.get("/evaluations")
async def get_evaluations():
    """Return the aggregate evaluation summary and per-query evaluation records.

    Implements the observability view for thesis Section 4.6-4.7 metrics
    (Tool F1, answer correctness rubric) collected per query.
    """
    if _orchestrator is None:
        return {"summary": {"total_evaluations": 0}, "records": []}
    evaluator = _orchestrator.evaluator
    return {
        "summary": evaluator.get_evaluation_summary(),
        "records": evaluator._evaluation_records,
    }
