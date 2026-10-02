from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TextPricing:
    model: str
    version: str
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    long_context_above: int | None = None
    long_input_multiplier: Decimal = Decimal(1)
    long_output_multiplier: Decimal = Decimal(1)
    cache_write_multiplier: Decimal | None = None

    def __post_init__(self):
        numbers = (
            self.input_per_million,
            self.cached_input_per_million,
            self.output_per_million,
            self.long_input_multiplier,
            self.long_output_multiplier,
        )
        if any(not value.is_finite() or value < 0 for value in numbers):
            raise ValueError("Pricing must be finite and non-negative")
        if self.cache_write_multiplier is not None and (
            not self.cache_write_multiplier.is_finite()
            or self.cache_write_multiplier < 0
        ):
            raise ValueError("Cache-write pricing must be finite and non-negative")
        if not self.model or not self.version:
            raise ValueError("Model and pricing version are required")


def observed_text_cost(
    usage: dict[str, Any] | None,
    *,
    model: str,
    pricing: TextPricing,
    service_tier: str = "default",
) -> dict[str, Any]:
    result = {
        "usd": None,
        "complete": False,
        "pricing_version": pricing.version,
        "basis": "observed_text_usage",
    }
    if model != pricing.model or service_tier != "default":
        return {**result, "reason": "unsupported_model_or_service_tier"}
    if usage is None:
        return {**result, "reason": "usage_unavailable_potentially_billable"}
    tokens = [
        usage.get(key)
        for key in ("input_tokens", "cached_input_tokens", "output_tokens")
    ]
    if any(type(value) is not int or value < 0 for value in tokens):
        return {**result, "reason": "missing_or_invalid_usage"}
    input_tokens, cached, output_tokens = tokens
    if cached > input_tokens:
        return {**result, "reason": "cached_tokens_exceed_input"}
    writes = usage.get("cache_write_tokens", 0)
    if type(writes) is not int or writes < 0 or cached + writes > input_tokens:
        return {**result, "reason": "missing_or_invalid_cache_write_usage"}
    if (writes and pricing.cache_write_multiplier is None) or usage.get(
        "paid_tool_fees_unknown", False
    ):
        return {**result, "reason": "additional_billing_not_supported"}
    long = (
        pricing.long_context_above is not None
        and input_tokens > pricing.long_context_above
    )
    input_multiplier = pricing.long_input_multiplier if long else Decimal(1)
    output_multiplier = pricing.long_output_multiplier if long else Decimal(1)
    cost = (
        (input_tokens - cached - writes) * pricing.input_per_million * input_multiplier
        + writes
        * pricing.input_per_million
        * (
            pricing.cache_write_multiplier
            if pricing.cache_write_multiplier is not None
            else Decimal(1)
        )
        * input_multiplier
        + cached * pricing.cached_input_per_million * input_multiplier
        + output_tokens * pricing.output_per_million * output_multiplier
    ) / Decimal(1_000_000)
    return {
        **result,
        "usd": str(cost),
        "complete": True,
        "reason": "calculated",
        "scope": "text API cost only, not an invoice or local hardware cost",
    }


def aggregate_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for call in calls:
        call_id = call["call_id"]
        if call_id in unique and unique[call_id] != call:
            raise ValueError("Conflicting records for the same model-call attempt")
        unique[call_id] = call
    remote = [call for call in unique.values() if call["executor"] == "ESCALATE"]
    unknown = sum(not call["cost"]["complete"] for call in remote)
    known = sum(
        (Decimal(call["cost"]["usd"]) for call in remote if call["cost"]["complete"]),
        Decimal(0),
    )
    return {
        "remote_call_attempts": len(remote),
        "unknown_cost_attempts": unknown,
        "known_api_cost_usd": str(known),
        "api_cost_complete": unknown == 0,
        "estimated_always_remote_cost_usd": None,
        "estimated_cost_avoided_usd": None,
    }
