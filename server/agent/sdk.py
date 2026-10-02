from __future__ import annotations

import httpx
from agents import ModelSettings, RunConfig
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from agents.retry import ModelRetrySettings
from openai import AsyncOpenAI
from openai.types.shared import Reasoning

from .contract import lora_settings


def execution_run_config() -> RunConfig:
    return RunConfig(tracing_disabled=True)


def local_settings(adapter_id: int) -> ModelSettings:
    return ModelSettings(
        temperature=0,
        include_usage=True,
        parallel_tool_calls=False,
        extra_body={**lora_settings(adapter_id, decision=False), "cache_prompt": False},
        retry=ModelRetrySettings(max_retries=0),
    )


def remote_settings(name: str = "") -> ModelSettings:
    return ModelSettings(
        parallel_tool_calls=False,
        store=False,
        reasoning=Reasoning(effort="medium") if name == "gpt-5.6-luna" else None,
        extra_args={"service_tier": "default"} if name == "gpt-5.6-luna" else None,
        retry=ModelRetrySettings(max_retries=0),
    )


def local_model(
    name: str, base_url: str, api_key: str = "not-needed"
) -> tuple[OpenAIChatCompletionsModel, AsyncOpenAI]:
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=0,
        timeout=120,
        http_client=httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(120, connect=5, pool=5),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        ),
    )
    return OpenAIChatCompletionsModel(model=name, openai_client=client), client


def remote_model(name: str, api_key: str) -> tuple[OpenAIResponsesModel, AsyncOpenAI]:
    client = AsyncOpenAI(
        api_key=api_key,
        max_retries=0,
        timeout=120,
        http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=5, pool=5),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        ),
    )
    return OpenAIResponsesModel(model=name, openai_client=client), client
