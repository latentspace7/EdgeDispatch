from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_STATES = {
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "needs_reconciliation",
}
POLICIES = {"reconsider_each_turn", "sticky_escalation"}


class NotFoundError(KeyError):
    pass


class ConflictError(ValueError):
    pass


class Ledger:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "events.jsonl"
        self._lock_file = (directory / ".writer.lock").open("a+")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise RuntimeError(
                "Only one backend worker may own the JSONL ledger"
            ) from None
        self._mutex = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._healthy = True
        try:
            self._load()
        except BaseException:
            self._lock_file.close()
            raise

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        lines = raw.splitlines(keepends=True)
        valid_bytes = 0
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                if index != len(lines) - 1 or line.endswith(b"\n"):
                    raise RuntimeError(
                        "Corrupt ledger record requires manual recovery"
                    ) from None
                backup = self.path.with_name(f"events.torn-{uuid.uuid4().hex}.jsonl")
                with backup.open("xb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._sync_directory()
                with self.path.open("r+b") as handle:
                    handle.truncate(valid_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                break
            if (
                not isinstance(event, dict)
                or event.get("sequence") != index + 1
                or event.get("schema_version") != 1
            ):
                raise RuntimeError("Invalid ledger sequence or schema version")
            self._events.append(event)
            valid_bytes += len(line)
        else:
            if raw and not raw.endswith(b"\n"):
                with self.path.open("ab") as handle:
                    handle.write(b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())

    def _sync_directory(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def append(
        self,
        kind: str,
        conversation_id: str,
        turn_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._mutex:
            if not self._healthy or self._lock_file.closed:
                raise RuntimeError(
                    "Ledger is unavailable; no further actions are permitted"
                )
            event = {
                "schema_version": 1,
                "sequence": len(self._events) + 1,
                "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(UTC).isoformat(),
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "type": kind,
                "payload": copy.deepcopy(payload),
            }
            encoded = (
                json.dumps(
                    event, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode()
                + b"\n"
            )
            try:
                created = not self.path.exists()
                with self.path.open("ab") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                if created:
                    self._sync_directory()
            except OSError:
                self._healthy = False
                raise
            self._events.append(event)
            return copy.deepcopy(event)

    def events(
        self,
        *,
        after: int = 0,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._mutex:
            return copy.deepcopy(
                [
                    event
                    for event in self._events
                    if event["sequence"] > after
                    and (
                        conversation_id is None
                        or event["conversation_id"] == conversation_id
                    )
                    and (turn_id is None or event["turn_id"] == turn_id)
                ]
            )

    def create_turn(
        self,
        conversation_id: str,
        request_id: str,
        query: str,
        settings: dict[str, Any],
        *,
        force_remote: bool = False,
    ) -> tuple[str, bool]:
        if not query.strip() or not request_id or not conversation_id:
            raise ValueError("Conversation ID, request ID and message are required")
        if settings.get("policy") not in POLICIES:
            raise ValueError("Unknown escalation policy")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    "query": query,
                    "force_remote": force_remote,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        with self._mutex:
            for event in self._events:
                if (
                    event["type"] == "turn_created"
                    and event["payload"]["request_id"] == request_id
                ):
                    if event["payload"]["fingerprint"] != fingerprint:
                        raise ConflictError(
                            "Request ID was already used with different content"
                        )
                    return event["turn_id"], False
            for turn in self.turns(conversation_id):
                if turn["state"] not in TERMINAL_STATES:
                    raise ConflictError("This conversation already has an active turn")
                if turn["state"] == "needs_reconciliation":
                    raise ConflictError(
                        "An earlier action needs reconciliation before continuing"
                    )
            turn_id = str(uuid.uuid4())
            self.append(
                "turn_created",
                conversation_id,
                turn_id,
                {
                    "request_id": request_id,
                    "fingerprint": fingerprint,
                    "query": query,
                    "settings": settings,
                    "force_remote": force_remote,
                },
            )
            return turn_id, True

    def turns(self, conversation_id: str | None = None) -> list[dict[str, Any]]:
        turns: dict[str, dict[str, Any]] = {}
        for event in self.events(conversation_id=conversation_id):
            turn_id = event["turn_id"]
            payload = event["payload"]
            if event["type"] == "turn_created":
                turns[turn_id] = {
                    "id": turn_id,
                    "conversation_id": event["conversation_id"],
                    "state": "queued",
                    "created_at": event["timestamp"],
                    **payload,
                    "answer": "",
                    "attempt_id": None,
                    "decision": None,
                }
            if turn_id not in turns:
                continue
            turn = turns[turn_id]
            if event["type"] in {"turn_state", "turn_finished"}:
                turn.update(payload)
            elif event["type"] == "decision":
                turn["decision"] = payload
            elif event["type"] == "attempt_started":
                turn.update(
                    attempt_id=payload["attempt_id"],
                    answer="",
                    executor=payload["executor"],
                )
            elif (
                event["type"] == "answer_delta"
                and payload["attempt_id"] == turn["attempt_id"]
            ):
                turn["answer"] += payload["text"]
            elif event["type"] == "answer_superseded":
                turn.update(answer="", attempt_id=None)
        return list(turns.values())

    def recover_interrupted(self) -> list[str]:
        recovered = []
        for turn in self.turns():
            if turn["state"] in TERMINAL_STATES:
                continue
            pending = self.pending_actions(turn["id"])
            state = "needs_reconciliation" if pending else "interrupted"
            self.append(
                "turn_finished",
                turn["conversation_id"],
                turn["id"],
                {
                    "state": state,
                    "reason": "process_restart",
                    "pending_actions": sorted(pending),
                },
            )
            recovered.append(turn["id"])
        return recovered

    def pending_actions(self, turn_id: str) -> set[str]:
        pending: set[str] = set()
        for event in self.events(turn_id=turn_id):
            payload = event["payload"]
            if event["type"] == "tool_intent" and payload["side_effect"]:
                pending.add(payload["action_id"])
            elif event["type"] == "tool_result":
                pending.discard(payload["action_id"])
        return pending

    def close(self) -> None:
        with self._mutex:
            if not self._lock_file.closed:
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
                self._lock_file.close()
