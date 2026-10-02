from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from os import environ
from urllib.parse import urlparse

from server.agent.history import conversation_messages

RUBRIC_VERSION = "answer-quality-phoenix-v1"


def judge_parameters(model: str) -> dict:
    if model == "gpt-5.6-luna":
        return {
            "temperature": 1,
            "reasoning_effort": "medium",
            "service_tier": "default",
        }
    return {}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if re.search(
                r"api.?key|authorization|password|secret|access.?token",
                k,
                re.IGNORECASE,
            )
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    patterns = [
        r"\b(?:sk-|hf_|rpa_)[A-Za-z0-9_.-]{12,}",
        r"(?i)\bBearer\s+[A-Za-z0-9_.-]+",
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    ]
    for pattern in patterns:
        value = re.sub(
            pattern, lambda m: "[redacted-" + digest(m.group())[:8] + "]", value
        )
    return value


@dataclass
class QualityConfig:
    enabled: bool = field(
        default_factory=lambda: (
            environ.get("EDGE_QUALITY_ENABLED", "false").lower() == "true"
        )
    )
    consent: bool = field(
        default_factory=lambda: (
            environ.get("EDGE_QUALITY_ALLOW_CONTENT", "false").lower() == "true"
        )
    )
    model: str = field(default_factory=lambda: environ.get("EDGE_QUALITY_MODEL", ""))
    key: str = field(
        default_factory=lambda: (
            environ.get("EDGE_QUALITY_API_KEY") or environ.get("OPENAI_API_KEY", "")
        )
    )
    base_url: str = field(
        default_factory=lambda: environ.get(
            "EDGE_QUALITY_BASE_URL", "https://api.openai.com/v1"
        )
    )
    host: str = field(
        default_factory=lambda: environ.get("PHOENIX_BASE_URL", "").rstrip("/")
    )
    phoenix_key: str = field(default_factory=lambda: environ.get("PHOENIX_API_KEY", ""))
    project: str = field(
        default_factory=lambda: environ.get("PHOENIX_PROJECT_NAME", "edgedispatch")
    )
    retrieval_tools: str = field(
        default_factory=lambda: environ.get(
            "EDGE_QUALITY_RETRIEVAL_TOOLS",
            "customer_search,customer_get,booking_list,booking_get,policy_search,policy_get,support_case_list,support_case_get,refund_list,read_document",
        )
    )
    max_chars: int = field(
        default_factory=lambda: int(environ.get("EDGE_QUALITY_MAX_CHARS", "120000"))
    )

    @property
    def export_enabled(self):
        return bool(self.host and self.project)

    @property
    def problem(self):
        if not self.enabled:
            return "disabled"
        if not self.consent:
            return "Content sharing must be explicitly enabled"
        if not self.model or not self.key:
            return "Configure EDGE_QUALITY_MODEL and a judge API key"
        for url in (self.base_url, self.host):
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                return "Service URLs must not contain credentials, query strings or fragments"
            if parsed.scheme != "https" and not (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1"}
            ):
                return "Use HTTPS, or HTTP on localhost only"
        if self.host and not self.project:
            return "Configure PHOENIX_PROJECT_NAME"
        if self.phoenix_key and not self.host:
            return "Configure PHOENIX_BASE_URL"
        if self.max_chars < 1000:
            return "EDGE_QUALITY_MAX_CHARS must be at least 1000"
        return ""

    @property
    def fingerprint(self):
        try:
            evaluator_version = version("arize-phoenix-evals")
        except PackageNotFoundError:
            evaluator_version = "missing"
        return digest(
            [
                RUBRIC_VERSION,
                self.model,
                self.base_url,
                evaluator_version,
                self.retrieval_tools,
                self.max_chars,
            ]
            + ([judge_parameters(self.model)] if judge_parameters(self.model) else [])
        )[:16]


def make_evidence(events: list[dict], finished: dict, config: QualityConfig) -> dict:
    history = [
        e
        for e in events
        if e["conversation_id"] == finished["conversation_id"]
        and e["sequence"] <= finished["sequence"]
    ]
    current = [e for e in history if e.get("turn_id") == finished["turn_id"]]
    request = next(
        e["payload"]["query"] for e in current if e["type"] == "turn_created"
    )
    tools = next(
        (e["payload"]["tools"] for e in reversed(current) if e["type"] == "catalogue"),
        [],
    )
    errors = {
        e["payload"].get("action_id") for e in history if e["type"] == "tool_error"
    }
    retrieval = {s.strip() for s in config.retrieval_tools.split(",") if s.strip()}
    contexts = list(
        dict.fromkeys(
            str(e["payload"]["content"])
            for e in history
            if e["type"] == "tool_result"
            and e["payload"].get("tool") in retrieval
            and e["payload"].get("action_id") not in errors
            and e["payload"].get("content")
        )
    )
    return redact(
        {
            "request": request,
            "messages": conversation_messages(history),
            "available_tools": tools,
            "tool_activity": [
                {"type": e["type"], "payload": e["payload"]}
                for e in history
                if e["type"]
                in {
                    "tool_intent",
                    "tool_result",
                    "tool_error",
                    "approval_requested",
                    "approval_resolved",
                }
            ],
            "answer": finished["payload"].get("answer", ""),
            "retrieved_contexts": contexts,
        }
    )
