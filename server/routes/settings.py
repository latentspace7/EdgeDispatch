from fastapi import APIRouter, Request

from server.agent.settings import Preferences

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=Preferences)
async def get_settings(request: Request):
    return request.app.state.runtime.preferences()


@router.put("/settings", response_model=Preferences)
async def save_settings(body: Preferences, request: Request):
    runtime = request.app.state.runtime
    runtime.save_preferences(body)
    return runtime.preferences()
