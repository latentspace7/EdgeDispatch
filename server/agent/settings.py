from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .contract import BASE_MODEL

ROOT = Path(__file__).resolve().parents[2]


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    policy: Literal["reconsider_each_turn", "sticky_escalation"] = "sticky_escalation"
    pricing_model: str = ""
    pricing_version: str = ""
    input_rate: float | None = Field(default=None, ge=0)
    cached_input_rate: float | None = Field(default=None, ge=0)
    output_rate: float | None = Field(default=None, ge=0)


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGE_", populate_by_name=True)

    artifacts_dir: Path = ROOT.parent / "thesis_artifacts"
    data_dir: Path = ROOT / "runtime_data"
    redis_url: str = "redis://127.0.0.1:6379/0"
    local_url: str = Field(
        default="http://127.0.0.1:8080/v1", validation_alias="EDGE_LOCAL_BASE_URL"
    )
    local_model: str = Field(
        default=BASE_MODEL, validation_alias="EDGE_LOCAL_MODEL_NAME"
    )
    local_key: str = Field(
        default="not-needed", validation_alias="EDGE_LOCAL_API_KEY", repr=False
    )
    adapter_path: str = Field(
        default_factory=lambda data: str(
            data["artifacts_dir"] / "models/execution-decision-adapter.gguf"
        )
    )
    remote_model: str = Field(
        default="gpt-5.6-luna", validation_alias="EDGE_HIGH_END_MODEL_NAME"
    )
    remote_key: str = Field(default="", validation_alias="OPENAI_API_KEY", repr=False)
    context_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=1024, ge=128)
    decision_mode: Literal["direct"] = "direct"
    max_turns: int = Field(default=10, ge=1, validation_alias="EDGE_MAX_MODEL_CALLS")
    concurrency: int = Field(default=1, ge=1)
    serving_report: Path = Field(
        default_factory=lambda data: data["artifacts_dir"] / "models/serving-check.json"
    )
    default_policy: Literal["reconsider_each_turn", "sticky_escalation"] = Field(
        default="sticky_escalation", validation_alias="EDGE_DEFAULT_ESCALATION_POLICY"
    )

    @field_validator("data_dir", "artifacts_dir", "serving_report")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return value.resolve()

    @field_validator("adapter_path")
    @classmethod
    def resolve_adapter_path(cls, value: str) -> str:
        return str(Path(value).resolve())

    @field_validator("local_url")
    @classmethod
    def validate_local_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.port == 0
        ):
            raise ValueError("Invalid local server URL")
        return value

    @model_validator(mode="after")
    def validate_context(self) -> AppConfig:
        if self.context_tokens and self.output_tokens >= self.context_tokens:
            raise ValueError("Output limit must be smaller than context")
        return self


def mcp_configs() -> list[dict]:
    path = os.getenv("EDGE_MCP_CONFIG")
    if path:
        return json.loads(Path(path).read_text())
    return [
        {
            "name": name,
            "command": sys.executable,
            "args": ["-m", f"mcp_server_{module}"],
            "cwd": str(ROOT),
        }
        for name, module in [
            ("customer_bookings", "db"),
            ("airline_policy", "wiki"),
            ("support_crm", "crm"),
        ]
    ]
