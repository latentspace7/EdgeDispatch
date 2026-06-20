"""
EdgeDispatch - Chat Routes

Endpoints for chat streaming (SSE) and conversation management.
"""

from __future__ import annotations

import asyncio
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
                    "message": "Analyzing query tool requirements...",
                    "conversation_id": conv_id,
                    "message_id": user_msg.id,
                }),
            }

            await asyncio.sleep(0.1)

            # Process through orchestrator if available
            if _orchestrator is not None:
                # Update orchestrator threshold
                _orchestrator.set_threshold(threshold)

                # Pre-analyze for tool count
                tool_analysis = _orchestrator.router.analyze_query(
                    query, _orchestrator.tool_manifest
                )
                estimated_tools = tool_analysis["estimated_tool_count"]
                route = tool_analysis["route_decision"]

                yield {
                    "event": "status",
                    "data": json.dumps({
                        "type": "routing",
                        "message": (
                            f"Estimated {estimated_tools} tool(s) needed. "
                            f"Threshold: {threshold}. "
                            f"Routing: {'cloud' if route == 'escalated' else 'local'}."
                        ),
                        "estimated_tools": estimated_tools,
                        "route_decision": route,
                        "threshold": threshold,
                    }),
                }

                await asyncio.sleep(0.1)

                # Process query
                result = await _orchestrator.process_query(query)
                response_text = result.final_answer

                # Stream the response in chunks (simulate token-by-token)
                words = response_text.split()
                chunk_size = max(1, len(words) // 20)  # ~20 chunks total

                for i in range(0, len(words), chunk_size):
                    chunk = " ".join(words[i : i + chunk_size])
                    if i > 0:
                        chunk = " " + chunk
                    assistant_content += chunk
                    yield {
                        "event": "token",
                        "data": chunk,
                    }
                    await asyncio.sleep(0.03)

                # Emit done with metadata + cost breakdown
                done_payload: dict[str, Any] = {
                    "conversation_id": conv_id,
                    "was_escalated": result.was_escalated,
                    "tool_count": result.tool_count,
                    "threshold": result.tool_threshold,
                }
                if result.cost is not None:
                    done_payload["cost"] = result.cost.as_dict()
                if result.evaluation:
                    done_payload["evaluation"] = result.evaluation

                yield {
                    "event": "done",
                    "data": json.dumps(done_payload),
                }
            else:
                # No orchestrator — return a mock message
                mock_response = (
                    f"Received your query: \"{query}\". (Orchestrator not configured — "
                    "connect MCP servers and models to enable full processing.)"
                )
                assistant_content = mock_response
                yield {
                    "event": "token",
                    "data": mock_response,
                }
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "conversation_id": conv_id,
                        "was_escalated": False,
                        "tool_count": 0,
                        "threshold": threshold,
                    }),
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

    return EventSourceResponse(event_generator())


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
