from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_MODEL = "LiquidAI/LFM2.5-2.6B"
ACCEPTED_REPORT_SHA256 = (
    "2f7abf66b77e0430d3755f6c97b01da6f780f2d3f2752f82893b16646e74b9e2"
)
SYSTEM_PROMPT = (
    "You are the EdgeDispatch execution router. Given the conversation and "
    "available tools, return exactly LOCAL or ESCALATE. Use ESCALATE when the "
    "user explicitly requests escalation, a required capability is unavailable, "
    "or reliable completion by the local executor is doubtful. Otherwise return "
    "LOCAL."
)
REQUEST_KEYS = {"messages", "tools", "tool_choice", "parallel_tool_calls"}


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def runtime_checks_passed(report: dict[str, Any]) -> bool:
    if not isinstance(report, dict) or report.get("error"):
        return False
    checks = report.get("checks")
    return isinstance(checks, dict) and all(
        checks.get(name) is True
        for name in (
            "token_parity",
            "adapter_zero_matches_base",
            "interleaved_scale_isolation",
            "tool_call_and_continuation",
        )
    )


def validate_request(request: dict[str, Any]) -> None:
    if set(request) != REQUEST_KEYS:
        raise ValueError(
            "Decision input must contain exactly the four trained request fields"
        )
    messages = request["messages"]
    if not isinstance(messages, list) or not messages:
        raise ValueError("A non-empty conversation is required")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise ValueError("Unsupported message role")
        tool_call_only = (
            message["role"] == "assistant"
            and message.get("content") is None
            and bool(message.get("tool_calls"))
        )
        if not isinstance(message.get("content"), str) and not tool_call_only:
            raise ValueError(
                "This integration supports text history only; attachments need escalation"
            )
    if messages[-1]["role"] != "user":
        raise ValueError("Classify only a new user turn, not tool continuations")
    if not isinstance(request["tools"], list):
        raise TypeError("Tools must be a list")
    for tool in request["tools"]:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ValueError("Only function tools are supported")
        function = tool.get("function", {})
        if not isinstance(function, dict):
            raise TypeError("Malformed function tool")
        if not isinstance(function.get("name"), str) or not isinstance(
            function.get("parameters"), dict
        ):
            raise TypeError("Malformed function tool")
    if not isinstance(request["parallel_tool_calls"], bool):
        raise TypeError("parallel_tool_calls must be boolean")
    if request["tool_choice"] not in ("auto", "none", "required") and not isinstance(
        request["tool_choice"], dict
    ):
        raise ValueError("Unsupported tool choice")


def decision_messages(request: dict[str, Any]) -> list[dict[str, str]]:
    validate_request(request)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(request, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def decision_prefix(request: dict[str, Any]) -> str:
    messages = decision_messages(request)
    return (
        "<|startoftext|>"
        + "".join(
            f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n"
            for message in messages
        )
        + "<|im_start|>assistant\n"
    )


def lora_settings(adapter_id: int, *, decision: bool) -> dict[str, Any]:
    if type(adapter_id) is not int or adapter_id < 0:
        raise ValueError("Invalid loaded adapter ID")
    return {"lora": [{"id": adapter_id, "scale": 1.0 if decision else 0.0}]}


@dataclass(frozen=True)
class Decision:
    route: str
    reason: str
    raw: str = ""


def parse_decision(raw: str) -> Decision:
    clean = raw.strip()
    if clean.endswith("<|im_end|>"):
        clean = clean[: -len("<|im_end|>")].strip()
    if clean in {"LOCAL", "ESCALATE"}:
        return Decision(clean, "adapter_prediction", raw)
    return Decision("ESCALATE", "invalid_decision_output", raw)


def verify_artifacts(
    artifacts_dir: Path, adapter_path: Path, report_path: Path
) -> dict[str, Any]:
    if sha256_file(report_path) != ACCEPTED_REPORT_SHA256:
        raise ValueError("Serving report is not the accepted thesis candidate")
    report = json.loads(report_path.read_text())
    if not runtime_checks_passed(report):
        raise ValueError("Serving report did not pass the required runtime checks")
    paths = {
        "base": adapter_path.parent / "lfm-base-Q8_0.gguf",
        "adapter": adapter_path,
        "source_adapter": artifacts_dir / "adapter/adapter_model.safetensors",
        "selection": artifacts_dir / "accepted_candidate_seal.json",
    }
    for name, path in paths.items():
        if sha256_file(path) != report[f"{name}_sha256"]:
            raise ValueError(f"Accepted {name} checksum mismatch")
    return report
