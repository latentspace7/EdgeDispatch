from __future__ import annotations

from typing import Any


def response_messages(items: list[Any]) -> list[dict]:
    messages = []
    for value in items:
        item = value.model_dump() if hasattr(value, "model_dump") else value
        if item.get("type") == "message":
            text = "".join(
                part.get("text", part.get("refusal", ""))
                for part in item.get("content", [])
            )
            if text:
                messages.append({"role": "assistant", "content": text})
        elif item.get("type") == "function_call":
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": item["call_id"],
                            "type": "function",
                            "function": {
                                "name": item["name"],
                                "arguments": item["arguments"],
                            },
                        }
                    ],
                }
            )
    return messages


def sdk_input(messages: list[dict]) -> list[dict]:
    result = []
    for message in messages:
        if message["role"] == "tool":
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
            continue
        if message.get("content"):
            result.append({"role": message["role"], "content": message["content"]})
        for call in message.get("tool_calls", []):
            result.append(
                {"type": "function_call", "call_id": call["id"], **call["function"]}
            )
    return result


def conversation_messages(events: list[dict]) -> list[dict]:
    messages = []
    pending: set[str] = set()

    def close_pending():
        for call_id in sorted(pending):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": "No confirmed result. Do not assume the action succeeded or repeat a write.",
                }
            )
        pending.clear()

    for event in events:
        if event["type"] == "turn_created":
            close_pending()
            messages.append({"role": "user", "content": event["payload"]["query"]})
        elif event["type"] == "history":
            for message in event["payload"]["messages"]:
                if message["role"] == "user":
                    close_pending()
                messages.append(message)
                for call in message.get("tool_calls", []):
                    pending.add(call["id"])
                if message["role"] == "tool":
                    pending.discard(message["tool_call_id"])
    close_pending()
    return messages
