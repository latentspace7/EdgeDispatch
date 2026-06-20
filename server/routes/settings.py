"""
EdgeDispatch - Settings Routes

Endpoints for reading and updating system configuration.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.config import (
    DEFAULT_TOOL_THRESHOLD,
    DEFAULT_PRICE_INPUT_PER_MTOK,
    DEFAULT_PRICE_OUTPUT_PER_MTOK,
    LOCAL_MODEL_NAME,
    HIGH_END_MODEL_NAME,
    ARIZE_PHOENIX_ENDPOINT,
)
from server.models.schemas import SettingsRequest, SettingsResponse

router = APIRouter(prefix="/api/settings", tags=["settings"])


# In-memory settings store (populated by the orchestrator)
_settings: dict = {
    "tool_threshold": DEFAULT_TOOL_THRESHOLD,
    "price_input_per_mtok": DEFAULT_PRICE_INPUT_PER_MTOK,
    "price_output_per_mtok": DEFAULT_PRICE_OUTPUT_PER_MTOK,
    "mcp_server_count": 0,
}


def get_current_threshold() -> int:
    """Get the current tool threshold."""
    return _settings.get("tool_threshold", DEFAULT_TOOL_THRESHOLD)


def set_current_threshold(threshold: int):
    """Set the current tool threshold."""
    _settings["tool_threshold"] = threshold


def get_current_pricing() -> tuple[float, float]:
    """Get the current (input, output) per-million-token prices."""
    return (
        _settings.get("price_input_per_mtok", DEFAULT_PRICE_INPUT_PER_MTOK),
        _settings.get("price_output_per_mtok", DEFAULT_PRICE_OUTPUT_PER_MTOK),
    )


def set_current_pricing(
    price_input_per_mtok: float | None = None,
    price_output_per_mtok: float | None = None,
):
    """Update pricing in the in-memory store. None leaves a value unchanged."""
    if price_input_per_mtok is not None:
        _settings["price_input_per_mtok"] = price_input_per_mtok
    if price_output_per_mtok is not None:
        _settings["price_output_per_mtok"] = price_output_per_mtok


def set_mcp_server_count(count: int):
    """Record the number of connected MCP servers (set by the orchestrator)."""
    _settings["mcp_server_count"] = max(0, count)


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Return the current system configuration."""
    return SettingsResponse(
        tool_threshold=get_current_threshold(),
        price_input_per_mtok=_settings["price_input_per_mtok"],
        price_output_per_mtok=_settings["price_output_per_mtok"],
        local_model=LOCAL_MODEL_NAME,
        high_end_model=HIGH_END_MODEL_NAME,
        mcp_server_count=_settings.get("mcp_server_count", 0),
        arize_endpoint=ARIZE_PHOENIX_ENDPOINT,
    )


@router.put("", response_model=SettingsResponse)
async def update_settings(body: SettingsRequest):
    """Update system settings (tool threshold and/or pricing)."""
    if body.tool_threshold is not None:
        if body.tool_threshold < 1:
            raise HTTPException(status_code=400, detail="tool_threshold must be >= 1")
        set_current_threshold(body.tool_threshold)

    if body.price_input_per_mtok is not None:
        if body.price_input_per_mtok < 0:
            raise HTTPException(
                status_code=400, detail="price_input_per_mtok must be >= 0"
            )
        set_current_pricing(price_input_per_mtok=body.price_input_per_mtok)

    if body.price_output_per_mtok is not None:
        if body.price_output_per_mtok < 0:
            raise HTTPException(
                status_code=400, detail="price_output_per_mtok must be >= 0"
            )
        set_current_pricing(price_output_per_mtok=body.price_output_per_mtok)

    return await get_settings()
