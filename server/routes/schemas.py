from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from server.agent.settings import Preferences


class TurnResponse(BaseModel):
    id: str
    conversation_id: str
    request_id: str
    query: str
    settings: Preferences
    force_remote: bool
    state: str
    created_at: str
    answer: str
    attempt_id: str | None
    decision: dict[str, JsonValue] | None
    executor: Literal["LOCAL", "ESCALATE"] | None = None
    reason: str | None = None
    error: str | None = None
    latency_ms: int | None = None
    quality_verified: bool | None = None
    quality: dict[str, JsonValue] | None = None
    approvals: list[dict[str, JsonValue]] = Field(default_factory=list)
    pending_actions: list[str] = Field(default_factory=list)


class ConversationSummaryResponse(BaseModel):
    id: str
    title: str
    policy: Literal["reconsider_each_turn", "sticky_escalation"]
    sticky: bool
    archived: bool
    created_at: str
    updated_at: str
    sequence: int
    metrics: dict[str, JsonValue]


class ConversationResponse(ConversationSummaryResponse):
    turns: list[TurnResponse]


class ConversationExport(BaseModel):
    conversation: ConversationResponse
    events: list[dict[str, JsonValue]]


class HealthResponse(BaseModel):
    status: Literal["ready", "setup_required"]
    redis: bool
    local_model: bool
    artifacts_verified: bool
    remote_configured: bool
    remote_access_tested: bool
    remote_access_error: str
    mcp_servers: list[str]
    unavailable_mcp_servers: list[str]
    storage: bool
    local_model_name: str
    remote_model_name: str
    context_tokens: int
    output_tokens: int
    decision_mode: str
    classifier_adapter_path: str
    decision_output_tokens: int
    decision_timeout_seconds: float
