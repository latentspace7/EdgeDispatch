from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .contract import (
    Decision,
    decision_prefix,
    lora_settings,
    parse_decision,
)


class TemplateResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    prompt: str


class TokenResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    tokens: list[int] = Field(min_length=1)


class DecisionClient:
    def __init__(
        self,
        base_url: str,
        adapter_path: str,
        *,
        context_tokens: int,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
        api_key: str = "not-needed",
    ) -> None:
        url = urlsplit(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid local server URL")
        self.mode = "direct"
        self.output_tokens = 6
        if context_tokens < 0 or 0 < context_tokens <= self.output_tokens:
            raise ValueError("Context must be automatic or leave room for the decision")
        if timeout <= 0:
            raise ValueError("Decision timeout must be positive")
        self.timeout = timeout
        self.adapter_path = adapter_path
        self.context_tokens = context_tokens
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/").removesuffix("/v1") + "/",
            timeout=httpx.Timeout(
                timeout, connect=min(5, timeout), pool=min(5, timeout)
            ),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            trust_env=False,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def tokenize(self, prompt: str) -> list[int]:
        response = await self.client.post(
            "tokenize",
            json={"content": prompt, "add_special": False, "parse_special": True},
        )
        response.raise_for_status()
        return TokenResponse.model_validate(response.json()).tokens

    async def check_execution_context(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_tokens: int,
    ) -> None:
        if not self.context_tokens:
            raise ValueError("Local model context has not been discovered")
        async with asyncio.timeout(self.timeout):
            response = await self.client.post(
                "apply-template",
                json={
                    "messages": messages,
                    "tools": tools,
                    "add_generation_prompt": True,
                },
            )
            response.raise_for_status()
            prompt = TemplateResponse.model_validate(response.json()).prompt
            tokens = await self.tokenize(prompt)
            if len(tokens) + output_tokens > self.context_tokens:
                raise ValueError(
                    "Local context limit exceeded; no history was truncated"
                )

    async def adapter_id(self) -> int:
        response = await self.client.get("lora-adapters")
        response.raise_for_status()
        adapters = response.json()
        if not isinstance(adapters, list) or any(
            not isinstance(item, dict) for item in adapters
        ):
            raise ValueError("Unexpected adapter discovery response")
        matches = [item for item in adapters if item.get("path") == self.adapter_path]
        if len(matches) != 1:
            raise ValueError("The configured decision adapter is not uniquely loaded")
        adapter_id = matches[0].get("id")
        if type(adapter_id) is not int:
            raise ValueError("The configured decision adapter has an invalid ID")
        return adapter_id

    async def classify(self, request: dict[str, Any]) -> Decision:
        try:
            async with asyncio.timeout(self.timeout):
                return await self._classify(request)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            return Decision("ESCALATE", "decision_unavailable")

    async def _classify(self, request: dict[str, Any]) -> Decision:
        prompt = decision_prefix(request)
        adapter_id = await self.adapter_id()
        tokens = await self.tokenize(prompt)
        if len(tokens) + 6 > self.context_tokens:
            return Decision("ESCALATE", "decision_context_exceeded")
        response = await self.client.post(
            "completion",
            json={
                "prompt": tokens,
                "n_predict": 6,
                "temperature": 0,
                "repeat_penalty": 1.1,
                "repeat_last_n": len(tokens) + 6,
                "stream": False,
                "stop": ["<|im_end|>"],
                "cache_prompt": False,
                **lora_settings(adapter_id, decision=True),
            },
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise TypeError("Unexpected completion response")
        if result.get("truncated") or result.get("tokens_evaluated", len(tokens)) < len(
            tokens
        ):
            return Decision("ESCALATE", "decision_context_truncated")
        raw = result.get("content")
        if not isinstance(raw, str):
            raise TypeError("Missing decision content")
        return parse_decision(raw)

    async def close(self) -> None:
        await self.client.aclose()
