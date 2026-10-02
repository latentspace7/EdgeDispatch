from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sse_starlette.sse import EventSourceResponse

from server.agent.storage import TERMINAL_STATES, ConflictError

from .schemas import (
    ConversationExport,
    ConversationResponse,
    ConversationSummaryResponse,
    TurnResponse,
)

router = APIRouter(prefix="/api", tags=["conversations"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    request_id: UUID
    query: str = Field(min_length=1, max_length=100_000)
    force_remote: bool = False


class ConversationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Literal["reconsider_each_turn", "sticky_escalation"]


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool


class Reconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=10, max_length=4000)


@router.post(
    "/chat",
    status_code=202,
    response_model=TurnResponse,
    response_model_exclude_unset=True,
)
async def chat(body: ChatRequest, request: Request):
    if not body.query.strip():
        raise HTTPException(400, "Query cannot be empty")
    try:
        return await request.app.state.runtime.submit(
            str(body.conversation_id),
            str(body.request_id),
            body.query,
            body.force_remote,
        )
    except ConflictError:
        raise
    except (OSError, RedisError, RuntimeError) as error:
        raise HTTPException(
            503,
            "Storage or Redis unavailable. No new execution was started. Retry with the same request ID.",
        ) from error


@router.get(
    "/conversations",
    response_model=list[ConversationSummaryResponse],
    response_model_exclude_unset=True,
)
async def conversations(request: Request):
    return request.app.state.runtime.conversations()


@router.post(
    "/conversations",
    status_code=201,
    response_model=ConversationResponse,
    response_model_exclude_unset=True,
)
async def new_conversation(request: Request):
    return request.app.state.runtime.create_conversation()


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    response_model_exclude_unset=True,
)
async def conversation(conversation_id: UUID, request: Request):
    return request.app.state.runtime.conversation(str(conversation_id))


@router.put(
    "/conversations/{conversation_id}/settings",
    response_model=ConversationResponse,
    response_model_exclude_unset=True,
)
async def conversation_settings(
    conversation_id: UUID, body: ConversationSettings, request: Request
):
    runtime = request.app.state.runtime
    runtime.conversation(str(conversation_id))
    runtime.emit("conversation_settings", str(conversation_id), None, body.model_dump())
    return runtime.conversation(str(conversation_id))


@router.post(
    "/conversations/{conversation_id}/reset-escalation",
    response_model=ConversationResponse,
    response_model_exclude_unset=True,
)
async def reset_escalation(conversation_id: UUID, request: Request):
    runtime = request.app.state.runtime
    return runtime.reset_escalation(str(conversation_id))


@router.delete("/conversations/{conversation_id}")
async def archive(conversation_id: UUID, request: Request) -> dict[str, bool]:
    runtime = request.app.state.runtime
    conversation = runtime.conversation(str(conversation_id))
    if any(turn["state"] not in TERMINAL_STATES for turn in conversation["turns"]):
        raise ConflictError("Cancel the active turn before archiving")
    runtime.emit(
        "conversation_settings", str(conversation_id), None, {"archived": True}
    )
    return {"archived": True, "recoverable": True}


@router.get(
    "/conversations/{conversation_id}/export",
    response_model=ConversationExport,
    response_model_exclude_unset=True,
)
async def export(conversation_id: UUID, request: Request):
    runtime = request.app.state.runtime
    conversation = runtime.conversation(str(conversation_id))
    return {
        "conversation": conversation,
        "events": runtime.ledger.events(conversation_id=str(conversation_id)),
    }


@router.get("/turns/{turn_id}/events")
async def turn_events(
    turn_id: UUID, request: Request, after: Annotated[int, Query(ge=0)] = 0
) -> EventSourceResponse:
    runtime = request.app.state.runtime
    runtime.turn(str(turn_id))
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        raise HTTPException(400, "Invalid event cursor") from None

    async def events() -> AsyncIterator[dict[str, str]]:
        nonlocal cursor
        while True:
            runtime.changed.clear()
            for event in runtime.ledger.events(turn_id=str(turn_id), after=cursor):
                cursor = event["sequence"]
                yield {"id": str(cursor), "event": "update", "data": json.dumps(event)}
            if runtime.turn(str(turn_id))["state"] in TERMINAL_STATES:
                yield {
                    "event": "settled",
                    "data": json.dumps(runtime.turn(str(turn_id))),
                }
                return
            try:
                await asyncio.wait_for(runtime.changed.wait(), timeout=15)
            except TimeoutError:
                yield {"comment": "keepalive"}

    return EventSourceResponse(
        events(), headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post(
    "/turns/{turn_id}/cancel",
    response_model=TurnResponse,
    response_model_exclude_unset=True,
)
async def cancel(turn_id: UUID, request: Request):
    runtime = request.app.state.runtime
    await runtime.cancel(str(turn_id))
    return runtime.turn(str(turn_id))


@router.post("/turns/{turn_id}/approvals/{action_id}")
async def approve(
    turn_id: UUID, action_id: UUID, body: Approval, request: Request
) -> dict[str, bool]:
    request.app.state.runtime.resolve_approval(
        str(turn_id), str(action_id), body.approved
    )
    return {"approved": body.approved}


@router.post(
    "/turns/{turn_id}/reconcile",
    response_model=TurnResponse,
    response_model_exclude_unset=True,
)
async def reconcile(turn_id: UUID, body: Reconciliation, request: Request):
    runtime = request.app.state.runtime
    turn = runtime.turn(str(turn_id))
    if turn["state"] != "needs_reconciliation":
        raise ConflictError("This turn does not need reconciliation")
    runtime.emit(
        "reconciliation", turn["conversation_id"], turn["id"], {"note": body.note}
    )
    runtime.emit(
        "history",
        turn["conversation_id"],
        turn["id"],
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Operator reconciliation of the interrupted action: "
                    + body.note,
                }
            ]
        },
    )
    runtime.emit(
        "turn_finished",
        turn["conversation_id"],
        turn["id"],
        {"state": "interrupted", "reason": "manually_reconciled"},
    )
    return runtime.turn(str(turn_id))
