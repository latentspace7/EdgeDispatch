from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from contextlib import AsyncExitStack, suppress
from dataclasses import asdict
from pathlib import Path

from agents import MaxTurnsExceeded
from openai import AuthenticationError, PermissionDeniedError
from redis.exceptions import RedisError

from .accounting import aggregate_calls
from .contract import Decision, verify_artifacts
from .coordination import ConversationLease, LeaseLost
from .decision import DecisionClient
from .discovery import discover_tools
from .executor import Execution
from .history import conversation_messages
from .policy import policy_decision
from .sdk import local_model, remote_model
from .settings import AppConfig, Preferences
from .storage import TERMINAL_STATES, ConflictError, Ledger, NotFoundError

logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, config: AppConfig, redis, servers: list, unavailable=()):
        self.config, self.redis, self.servers, self.unavailable = (
            config,
            redis,
            servers,
            unavailable,
        )
        self.ledger = Ledger(config.data_dir)
        try:
            self.ledger.recover_interrupted()
        except BaseException:
            self.ledger.close()
            raise
        self.changed = asyncio.Event()
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancelled: set[str] = set()
        self.approvals: dict[str, tuple[str, asyncio.Future]] = {}
        self.capacity = asyncio.Semaphore(config.concurrency)
        self.submission_lock = asyncio.Lock()
        self.decision = DecisionClient(
            config.local_url,
            config.adapter_path,
            context_tokens=config.context_tokens,
            api_key=config.local_key,
        )
        self.local, self.local_client = local_model(
            config.local_model, config.local_url, config.local_key
        )
        self.remote, self.remote_client = (
            remote_model(config.remote_model, config.remote_key)
            if config.remote_key
            else (None, None)
        )
        self.artifact_verified = self._artifact_status()
        self.remote_access_tested = False
        self.remote_access_error = ""
        self.local_model_name = ""

    async def local_ready(self):
        if not self.artifact_verified:
            return False
        try:
            report = self.serving_report
            await self.decision.adapter_id()
            response = await self.decision.client.get("props")
            response.raise_for_status()
            props = response.json()
            models = await self.decision.client.get("v1/models")
            models.raise_for_status()
            model = models.json()["data"][0]
            name = model["id"]
            native_context = model["meta"]["n_ctx_train"]
            loaded_context = props["default_generation_settings"]["n_ctx"]
            if (
                not isinstance(name, str)
                or not name
                or type(native_context) is not int
                or type(loaded_context) is not int
            ):
                return False
            available_context = min(native_context, loaded_context)
            context = self.config.context_tokens or available_context
            ready = (
                props.get("model_path")
                == str(Path(self.config.adapter_path).parent / "lfm-base-Q8_0.gguf")
                and props.get("build_info") == report["build_info"]
                and hashlib.sha256(props.get("chat_template", "").encode()).hexdigest()
                == report["chat_template_sha256"]
                and self.config.output_tokens < context <= available_context
            )
            if ready:
                self.decision.context_tokens = context
                self.local_model_name = name
                self.config.local_model = name
                self.local.model = name
            return ready
        except Exception:
            return False

    def _artifact_status(self) -> bool:
        if self.config.decision_mode != "direct":
            return False
        try:
            self.serving_report = verify_artifacts(
                self.config.artifacts_dir,
                Path(self.config.adapter_path),
                self.config.serving_report,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return True

    def emit(self, kind, conversation_id, turn_id, payload):
        event = self.ledger.append(kind, conversation_id, turn_id, payload)
        self.changed.set()
        return event

    def preferences(self) -> dict:
        values = Preferences(policy=self.config.default_policy).model_dump()
        for event in self.ledger.events(conversation_id=""):
            if event["type"] == "settings":
                values = event["payload"]
        return values

    def save_preferences(self, preferences: Preferences):
        self.emit("settings", "", None, preferences.model_dump())

    def conversation_config(self, conversation_id: str) -> dict:
        values = {
            "policy": self.preferences()["policy"],
            "sticky": False,
            "archived": False,
        }
        for event in self.ledger.events(conversation_id=conversation_id):
            if event["type"] in ("conversation_created", "conversation_settings"):
                values.update(event["payload"])
            elif (
                event["type"] == "decision" and event["payload"]["route"] == "ESCALATE"
            ) or (
                event["type"] == "attempt_started"
                and event["payload"]["executor"] == "ESCALATE"
            ):
                values["sticky"] = True
            elif event["type"] == "sticky_reset":
                values["sticky"] = False
        return values

    def reset_escalation(self, conversation_id: str) -> dict:
        conversation = self.conversation(conversation_id)
        if any(turn["state"] not in TERMINAL_STATES for turn in conversation["turns"]):
            raise ConflictError(
                "Wait for the active request to finish before classifying again"
            )
        self.emit("sticky_reset", conversation_id, None, {})
        return self.conversation(conversation_id)

    def create_conversation(self) -> dict:
        conversation_id = str(uuid.uuid4())
        self.emit(
            "conversation_created",
            conversation_id,
            None,
            {"policy": self.preferences()["policy"], "title": "New conversation"},
        )
        return self.conversation(conversation_id)

    def turn(self, turn_id: str) -> dict:
        for turn in self.ledger.turns():
            if turn["id"] == turn_id:
                return turn
        raise NotFoundError("Turn not found")

    def conversation(self, conversation_id: str) -> dict:
        events = self.ledger.events(conversation_id=conversation_id)
        if not events:
            raise NotFoundError("Conversation not found")
        turns = self.ledger.turns(conversation_id)
        settings = self.conversation_config(conversation_id)
        for turn in turns:
            if getattr(self, "quality", None):
                try:
                    turn["quality"] = self.quality.summary(turn["id"])
                except Exception:
                    turn["quality"] = {
                        "status": "unavailable",
                        "error": "Quality storage unavailable",
                    }
            turn["approvals"] = [
                {"id": action_id, **event["payload"]}
                for action_id, (tid, future) in self.approvals.items()
                if tid == turn["id"] and not future.done()
                for event in events
                if event["type"] == "approval_requested"
                and event["payload"]["action_id"] == action_id
            ]
        return {
            **settings,
            "id": conversation_id,
            "title": turns[0]["query"][:65]
            if turns
            else settings.get("title", "New conversation"),
            "created_at": events[0]["timestamp"],
            "updated_at": events[-1]["timestamp"],
            "turns": turns,
            "metrics": self.metrics(events, turns),
            "sequence": events[-1]["sequence"],
        }

    def conversations(self) -> list[dict]:
        ids = dict.fromkeys(
            event["conversation_id"]
            for event in self.ledger.events()
            if event["conversation_id"]
        )
        result = []
        for key in ids:
            conversation = self.conversation(key)
            if not conversation["archived"]:
                result.append({k: v for k, v in conversation.items() if k != "turns"})
        return sorted(result, key=lambda row: row["updated_at"], reverse=True)

    def metrics(self, events, turns):
        calls = {}
        for event in events:
            if event["type"] == "model_call_started":
                calls[event["payload"]["call_id"]] = {
                    **event["payload"],
                    "cost": {"complete": False, "usd": None},
                }
            elif event["type"] == "model_call_finished":
                calls[event["payload"]["call_id"]] = event["payload"]
        totals = aggregate_calls(list(calls.values()))
        remote_turns = {
            e["turn_id"]
            for e in events
            if e["type"] == "attempt_started" and e["payload"]["executor"] == "ESCALATE"
        }
        estimates = [
            event["payload"]["usd"]
            for event in events
            if event["type"] == "baseline_estimate"
        ]
        if estimates and len(estimates) == sum(
            t["state"] == "completed" for t in turns
        ):
            totals["estimated_always_remote_cost_usd"] = sum(estimates)
            if totals["api_cost_complete"] and all(
                t["state"] == "completed" for t in turns
            ):
                totals["estimated_cost_avoided_usd"] = sum(estimates) - float(
                    totals["known_api_cost_usd"]
                )
        return {
            **totals,
            "completed_local": sum(
                t["state"] == "completed"
                and t["id"] not in remote_turns
                and t.get("executor") == "LOCAL"
                for t in turns
            ),
            "remote_used_turns": len(remote_turns),
            "completed": sum(t["state"] == "completed" for t in turns),
            "pending": sum(t["state"] not in TERMINAL_STATES for t in turns),
            "failed_or_cancelled": sum(
                t["state"] in TERMINAL_STATES - {"completed"} for t in turns
            ),
            "decision_calls": sum(e["type"] == "decision_started" for e in events),
            "local_execution_calls": sum(
                c["executor"] == "LOCAL" for c in calls.values()
            ),
            "tool_calls": sum(e["type"] == "tool_intent" for e in events),
            "local_hardware_cost": "unmeasured",
            "quality_verified": False,
        }

    async def health(self):
        redis_ready = False
        local_ready = False
        try:
            redis_ready = bool(await self.redis.ping())
        except (RedisError, OSError) as error:
            logger.debug("Redis readiness failed (%s)", type(error).__name__)
        try:
            async with asyncio.timeout(3):
                local_ready = await self.local_ready()
        except TimeoutError:
            logger.debug("Local model readiness timed out")
        return {
            "status": "ready"
            if redis_ready
            and local_ready
            and self.remote is not None
            and not self.remote_access_error
            and not self.unavailable
            else "setup_required",
            "redis": redis_ready,
            "local_model": local_ready,
            "artifacts_verified": self.artifact_verified,
            "remote_configured": self.remote is not None,
            "remote_access_tested": self.remote_access_tested,
            "remote_access_error": self.remote_access_error,
            "mcp_servers": [server.name for server in self.servers],
            "unavailable_mcp_servers": list(self.unavailable),
            "storage": True,
            "local_model_name": self.local_model_name,
            "remote_model_name": self.config.remote_model,
            "context_tokens": self.decision.context_tokens,
            "output_tokens": self.config.output_tokens,
            "decision_mode": self.decision.mode,
            "classifier_adapter_path": self.config.adapter_path,
            "decision_output_tokens": self.decision.output_tokens,
            "decision_timeout_seconds": self.decision.timeout,
        }

    async def submit(
        self, conversation_id: str, request_id: str, query: str, force_remote: bool
    ):
        async with self.submission_lock:
            existing = next(
                (t for t in self.ledger.turns() if t["request_id"] == request_id), None
            )
            if existing:
                self.ledger.create_turn(
                    conversation_id,
                    request_id,
                    query,
                    existing["settings"],
                    force_remote=force_remote,
                )
                return existing
            if not await self.redis.ping():
                raise RuntimeError("Redis is unavailable")
            current = self.conversation_config(conversation_id)
            if current["archived"]:
                raise ConflictError("Archived conversation cannot accept new turns")
            if not self.ledger.events(conversation_id=conversation_id):
                self.emit(
                    "conversation_created",
                    conversation_id,
                    None,
                    {"policy": current["policy"]},
                )
            settings = {**self.preferences(), "policy": current["policy"]}
            turn_id, _ = self.ledger.create_turn(
                conversation_id, request_id, query, settings, force_remote=force_remote
            )
            self.changed.set()
            self.tasks[turn_id] = asyncio.create_task(self.run_turn(turn_id))
            return self.turn(turn_id)

    async def approve_tool(self, turn, action_id, tool, arguments):
        future = asyncio.get_running_loop().create_future()
        self.approvals[action_id] = (turn["id"], future)
        self.emit(
            "approval_requested",
            turn["conversation_id"],
            turn["id"],
            {"action_id": action_id, "tool": tool, "arguments": arguments},
        )
        try:
            return await asyncio.wait_for(future, timeout=300)
        except TimeoutError:
            self.emit(
                "approval_resolved",
                turn["conversation_id"],
                turn["id"],
                {"action_id": action_id, "approved": False, "reason": "expired"},
            )
            return False
        finally:
            self.approvals.pop(action_id, None)

    def resolve_approval(self, turn_id, action_id, approved):
        pending = self.approvals.get(action_id)
        if not pending or pending[0] != turn_id or pending[1].done():
            raise ConflictError("Approval is no longer pending")
        turn = self.turn(turn_id)
        self.emit(
            "approval_resolved",
            turn["conversation_id"],
            turn_id,
            {"action_id": action_id, "approved": approved},
        )
        pending[1].set_result(approved)

    async def cancel(self, turn_id):
        turn = self.turn(turn_id)
        if turn["state"] in TERMINAL_STATES:
            return
        self.cancelled.add(turn_id)
        self.emit("cancel_requested", turn["conversation_id"], turn_id, {})
        task = self.tasks.get(turn_id)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self.turn(turn_id)["state"] not in TERMINAL_STATES:
            self.emit(
                "turn_finished",
                turn["conversation_id"],
                turn_id,
                {"state": "cancelled", "reason": "cancelled_before_start"},
            )

    async def run_turn(self, turn_id):
        turn = self.turn(turn_id)
        cid = turn["conversation_id"]
        lease = ConversationLease(self.redis, cid)
        watcher = None
        started = time.monotonic()

        def emit(kind, payload):
            return self.emit(kind, cid, turn_id, payload)

        try:
            async with self.capacity:
                await lease.acquire()
                owner = asyncio.current_task()

                async def watch_lease():
                    await lease.lost.wait()
                    owner.cancel()

                watcher = asyncio.create_task(watch_lease())
                catalogue = await discover_tools(
                    self.servers, unavailable=tuple(self.unavailable)
                )
                emit(
                    "catalogue",
                    {
                        "tools": catalogue.tools,
                        "unavailable_servers": list(catalogue.unavailable_servers),
                    },
                )
                emit("turn_state", {"state": "deciding"})
                choice = policy_decision(
                    turn["settings"]["policy"],
                    force_remote=turn["force_remote"],
                    previously_escalated=self.conversation_config(cid)["sticky"],
                )
                if choice is None and catalogue.unavailable_servers:
                    choice = Decision("ESCALATE", "required_mcp_server_unavailable")
                if choice is None and not await self.local_ready():
                    choice = Decision("ESCALATE", "local_model_not_ready")
                messages = conversation_messages(
                    self.ledger.events(conversation_id=cid)
                )
                if choice is None:
                    await lease.ensure_owner()
                    emit("decision_started", {})
                    decision_start = time.monotonic()
                    choice = await self.decision.classify(
                        {
                            "messages": messages,
                            "tools": catalogue.tools,
                            "tool_choice": "auto",
                            "parallel_tool_calls": False,
                        }
                    )
                    emit(
                        "decision_finished",
                        {
                            "latency_ms": round(
                                (time.monotonic() - decision_start) * 1000
                            )
                        },
                    )
                emit("decision", asdict(choice))
                await lease.ensure_owner()
                emit("turn_state", {"state": "executing"})
                route = choice.route
                try:
                    adapter_id = (
                        await self.decision.adapter_id() if route == "LOCAL" else None
                    )
                    answer = await Execution(
                        self, turn, lease, catalogue, route, adapter_id
                    ).run(messages)
                except Exception as error:
                    if (
                        isinstance(error, LeaseLost)
                        or route != "LOCAL"
                        or self.ledger.pending_actions(turn_id)
                    ):
                        raise
                    await lease.ensure_owner()
                    emit("answer_superseded", {"reason": type(error).__name__})
                    emit(
                        "fallback",
                        {
                            "reason": str(error)
                            if isinstance(error, RuntimeError)
                            else type(error).__name__
                        },
                    )
                    route = "ESCALATE"
                    messages = conversation_messages(
                        self.ledger.events(conversation_id=cid)
                    )
                    answer = await Execution(
                        self, turn, lease, catalogue, route, None
                    ).run(messages)
                await lease.ensure_owner()
                self.estimate_baseline(turn, catalogue.tools, answer)
                emit(
                    "turn_finished",
                    {
                        "state": "completed",
                        "answer": answer,
                        "executor": route,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "quality_verified": False,
                    },
                )
        except asyncio.CancelledError:
            state = (
                "needs_reconciliation"
                if self.ledger.pending_actions(turn_id)
                else ("cancelled" if turn_id in self.cancelled else "interrupted")
            )
            emit(
                "turn_finished",
                {
                    "state": state,
                    "reason": "cancelled"
                    if turn_id in self.cancelled
                    else "lease_lost_or_shutdown",
                },
            )
        except Exception as error:
            state = (
                "needs_reconciliation"
                if self.ledger.pending_actions(turn_id)
                else "failed"
            )
            message = "Execution could not finish. Check model/Redis readiness and the activity log. No automatic retry was submitted."
            if isinstance(error, MaxTurnsExceeded):
                message = (
                    f"Execution reached the limit of {self.config.max_turns} model calls "
                    "for this attempt without a final answer."
                )
            elif isinstance(error, AuthenticationError):
                message = "OpenAI rejected the API key. Update OPENAI_API_KEY in .env and restart the backend."
                self.remote_access_error = message
            elif isinstance(error, PermissionDeniedError):
                message = "OpenAI denied access to the configured model. Check the API project's permissions and EDGE_HIGH_END_MODEL_NAME, then restart the backend."
                self.remote_access_error = message
            emit(
                "turn_finished",
                {
                    "state": state,
                    "reason": type(error).__name__,
                    "error": message,
                },
            )
        finally:
            if watcher:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            await lease.close()
            self.tasks.pop(turn_id, None)
            self.cancelled.discard(turn_id)

    def estimate_baseline(self, turn, tools, answer):
        settings = turn["settings"]
        if (
            settings.get("input_rate") is None
            or settings.get("output_rate") is None
            or not settings.get("pricing_model")
            or not settings.get("pricing_version")
        ):
            return
        from .executor import INSTRUCTIONS

        messages = conversation_messages(
            [
                e
                for e in self.ledger.events(conversation_id=turn["conversation_id"])
                if e["turn_id"] != turn["id"] or e["type"] == "turn_created"
            ]
        )
        text = INSTRUCTIONS + json.dumps(
            {"messages": messages, "tools": tools}, ensure_ascii=False
        )
        usd = (
            len(text) / 4 * settings["input_rate"]
            + len(answer) / 4 * settings["output_rate"]
        ) / 1_000_000
        self.emit(
            "baseline_estimate",
            turn["conversation_id"],
            turn["id"],
            {
                "usd": usd,
                "model": settings["pricing_model"],
                "pricing_version": settings["pricing_version"],
                "basis": "character_count_divided_by_four; one initial prompt and delivered answer; excludes unobserved reasoning, tool continuations and hardware",
            },
        )

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with AsyncExitStack() as stack:
            stack.callback(self.ledger.close)
            if self.remote_client:
                stack.push_async_callback(self.remote_client.close)
            stack.push_async_callback(self.local_client.close)
            stack.push_async_callback(self.decision.close)
