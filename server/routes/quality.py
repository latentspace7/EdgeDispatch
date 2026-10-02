from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, JsonValue

from server.quality.service import QualityService

router = APIRouter(prefix="/api/quality", tags=["answer-quality"])


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_trace_resend: bool = False


class BackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_paid_assessment: bool


def service(request: Request) -> QualityService:
    return request.app.state.runtime.quality


@router.get("/status")
async def status(request: Request) -> dict[str, JsonValue]:
    return service(request).status()


@router.get("/turns/{turn_id}")
async def turn_quality(turn_id: UUID, request: Request) -> dict[str, JsonValue]:
    request.app.state.runtime.turn(str(turn_id))
    return service(request).summary(str(turn_id))


@router.post("/backfill")
async def backfill(body: BackfillRequest, request: Request) -> dict[str, int]:
    worker = service(request)
    if worker.problem or not body.confirm_paid_assessment:
        raise HTTPException(
            409, worker.problem or "Explicit paid assessment confirmation is required"
        )
    return {"queued": worker.discover(backfill=True)}


@router.post("/turns/{turn_id}/retry")
async def retry(
    turn_id: UUID, body: RetryRequest, request: Request
) -> dict[str, JsonValue]:
    worker = service(request)
    if worker.problem:
        raise HTTPException(409, worker.problem)
    return worker.retry(str(turn_id), body.allow_trace_resend)
