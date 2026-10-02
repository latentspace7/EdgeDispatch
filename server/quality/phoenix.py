from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from datetime import datetime
from urllib.parse import quote

import httpx

from .evidence import digest, redact


def identifiers(turn_id):
    return digest(["edgedispatch-turn", turn_id])[:32], digest(
        ["edgedispatch-root", turn_id]
    )[:16]


def build_spans(events, finished, evidence):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import SpanContext, Status, StatusCode, TraceFlags

    trace_id, root_id = identifiers(finished["turn_id"])
    current = [
        e
        for e in events
        if e.get("turn_id") == finished["turn_id"]
        and e["sequence"] <= finished["sequence"]
    ]
    created = next(e for e in current if e["type"] == "turn_created")

    def context(sid):
        return SpanContext(int(trace_id, 16), int(sid, 16), False, TraceFlags(1))

    def ns(event):
        return int(
            datetime.fromisoformat(event["timestamp"]).timestamp() * 1_000_000_000
        )

    common = {
        "session.id": finished["conversation_id"],
        "edgedispatch.executor": finished["payload"].get("executor", "none"),
        "edgedispatch.delivery_state": finished["payload"]["state"],
        "edgedispatch.turn_id": finished["turn_id"],
        "deployment.environment": "local",
    }

    def span(name, sid, parent, start, end, attrs, error=False):
        return ReadableSpan(
            name,
            context=context(sid),
            parent=context(parent) if parent else None,
            resource=Resource.create({"service.name": "edgedispatch"}),
            attributes={
                **common,
                "input.mime_type": "application/json",
                "output.mime_type": "application/json",
                **attrs,
            },
            start_time=ns(start),
            end_time=ns(end),
            status=Status(StatusCode.ERROR if error else StatusCode.OK),
            instrumentation_scope=InstrumentationScope("edgedispatch.quality", "1.0"),
        )

    spans = [
        span(
            "EdgeDispatch answer",
            root_id,
            None,
            created,
            finished,
            {
                "openinference.span.kind": "AGENT",
                "input.value": json.dumps(
                    {k: v for k, v in evidence.items() if k != "answer"}
                ),
                "output.value": str(evidence.get("answer", "")),
                "output.mime_type": "text/plain",
            },
            finished["payload"]["state"] != "completed",
        )
    ]
    starts = {}
    attempts = [e for e in current if e["type"] == "attempt_started"]
    for index, attempt in enumerate(attempts):
        end = attempts[index + 1] if index + 1 < len(attempts) else finished
        spans.append(
            span(
                "Execution attempt",
                digest(attempt["payload"]["attempt_id"])[:16],
                root_id,
                attempt,
                end,
                {
                    "openinference.span.kind": "AGENT",
                    "input.value": json.dumps(redact(attempt["payload"])),
                    "edgedispatch.attempt_executor": attempt["payload"].get(
                        "executor", "unknown"
                    ),
                },
            )
        )
    parent_id = root_id
    for event in current:
        kind, p = event["type"], event["payload"]
        if kind == "attempt_started":
            parent_id = digest(p["attempt_id"])[:16]
        if kind in {
            "decision",
            "fallback",
            "approval_requested",
            "approval_resolved",
            "answer_superseded",
        }:
            spans.append(
                span(
                    kind,
                    digest(event["event_id"])[:16],
                    root_id,
                    event,
                    event,
                    {
                        "openinference.span.kind": "CHAIN",
                        "output.value": json.dumps(redact(p)),
                    },
                )
            )
        if kind in {"model_call_started", "tool_intent", "decision_started"}:
            key = (
                p.get("call_id")
                if kind == "model_call_started"
                else p.get("action_id")
                if kind == "tool_intent"
                else "decision"
            )
            starts[key] = event
        if kind in {
            "model_call_finished",
            "tool_result",
            "tool_error",
            "decision_finished",
        }:
            key = (
                p.get("call_id")
                if kind == "model_call_finished"
                else p.get("action_id")
                if kind.startswith("tool_")
                else "decision"
            )
            start = starts.pop(key, event)
            attrs = {
                "openinference.span.kind": "LLM"
                if kind == "model_call_finished"
                else "TOOL"
                if kind.startswith("tool_")
                else "CHAIN",
                "input.value": json.dumps(redact(start["payload"])),
                "output.value": json.dumps(redact(p)),
            }
            if kind == "model_call_finished":
                attrs["llm.model_name"] = p.get("model", "unknown")
                if p.get("usage"):
                    usage = p["usage"]
                    cached = usage.get("cached_input_tokens", 0)
                    attrs.update(
                        {
                            "llm.token_count.prompt": usage["input_tokens"],
                            "llm.token_count.completion": usage["output_tokens"],
                            "llm.token_count.total": usage["input_tokens"]
                            + usage["output_tokens"],
                            "llm.token_count.prompt_details.cache_read": cached,
                        }
                    )
                cost = p.get("cost", {})
                if cost.get("complete") and cost.get("usd") is not None:
                    attrs["llm.cost.total"] = float(cost["usd"])
            spans.append(
                span(
                    p.get("tool", kind),
                    digest(event["event_id"])[:16],
                    parent_id,
                    start,
                    event,
                    attrs,
                    kind == "tool_error",
                )
            )
    return spans


class PhoenixExport:
    def __init__(self, config):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from phoenix.client import AsyncClient

        self.config = config
        headers = (
            {"Authorization": "Bearer " + config.phoenix_key}
            if config.phoenix_key
            else {}
        )
        self.exporter = OTLPSpanExporter(
            endpoint=config.host + "/v1/traces",
            headers={**headers, "x-project-name": config.project},
            timeout=10,
        )
        self.http = httpx.AsyncClient(
            base_url=config.host + "/", headers=headers, timeout=15
        )
        self.client = AsyncClient(http_client=self.http)
        self.project_id = None

    async def traces(self, events, finished, evidence):
        from opentelemetry.sdk.trace.export import SpanExportResult

        result = await asyncio.to_thread(
            self.exporter.export, build_spans(events, finished, evidence)
        )
        if result != SpanExportResult.SUCCESS:
            raise RuntimeError("Trace export not confirmed")

    async def scores(self, job):
        trace_id, span_id = identifiers(job["turn_id"])
        for attempt in range(6):
            try:
                spans = await self.client.spans.get_spans(
                    project_identifier=self.config.project, span_ids=[span_id], limit=1
                )
                if spans:
                    break
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
            if attempt == 5:
                raise RuntimeError(
                    "Phoenix has not made the exported trace available yet"
                )
            await asyncio.sleep(min(0.25 * 2**attempt, 2))
        result = job["result"]
        rubric = result.get("rubric", {})
        values = dict(rubric.get("scores") or {})
        faithfulness = result.get("faithfulness", {})
        if faithfulness.get("value") is not None:
            values["phoenix_faithfulness"] = faithfulness["value"]
        for name in ("verdict", "outcome"):
            if name in rubric:
                values[name] = rubric[name]
        values["assessment_status"] = job["status"]
        values["delivery_state"] = job["delivery_state"]
        calls = result.get("judge_calls", [])
        if calls:
            values["judge_api_calls"] = len(calls)
            if all(call.get("usage") for call in calls):
                values["judge_tokens"] = sum(
                    call["usage"].get("total_tokens", 0) for call in calls
                )
        annotations = []
        for name, value in values.items():
            annotation_result = (
                {"label": value} if isinstance(value, str) else {"score": value}
            )
            annotation_result["explanation"] = rubric.get("rationale", "")
            if name == "phoenix_faithfulness":
                annotation_result.update(
                    label=faithfulness["label"], explanation=faithfulness["reason"]
                )
            annotations.append(
                {
                    "name": name,
                    "span_id": span_id,
                    "annotator_kind": "CODE"
                    if name
                    in {
                        "assessment_status",
                        "delivery_state",
                        "judge_api_calls",
                        "judge_tokens",
                    }
                    else "LLM",
                    "identifier": job["id"],
                    "result": annotation_result,
                    "metadata": redact(
                        {
                            "evaluation_version": job["version"],
                            "judge_model": job.get("judge_model", self.config.model),
                            "critical_issues": rubric.get("critical_issues", []),
                        }
                    ),
                }
            )
        await self.client.spans.log_span_annotations(
            span_annotations=annotations, sync=True
        )
        await self.client.traces.log_trace_annotations(
            trace_annotations=[
                {
                    **{k: v for k, v in annotation.items() if k != "span_id"},
                    "trace_id": trace_id,
                }
                for annotation in annotations
            ],
            sync=True,
        )
        if self.project_id is None:
            project = await self.client.projects.get(project_name=self.config.project)
            self.project_id = project["id"]
        job["trace_url"] = (
            self.config.host
            + "/projects/"
            + quote(self.project_id, safe="")
            + "/traces/"
            + trace_id
        )

    async def close(self) -> None:
        async with AsyncExitStack() as stack:
            stack.push_async_callback(asyncio.to_thread, self.exporter.shutdown)
            stack.push_async_callback(self.http.aclose)
