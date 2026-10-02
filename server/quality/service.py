from __future__ import annotations

import asyncio
import json
import sqlite3
from collections import Counter
from contextlib import AsyncExitStack
from datetime import UTC, datetime

from server.agent.storage import NotFoundError

from .evidence import QualityConfig, digest, make_evidence


def now():
    return datetime.now(UTC).isoformat()


class QualityService:
    def __init__(self, ledger, config=None):
        self.ledger = ledger
        try:
            self.config = config or QualityConfig()
            self.problem = self.config.problem
        except ValueError:
            self.config = QualityConfig(max_chars=120000)
            self.problem = "Invalid quality configuration"
        self.db = None
        self.task = None
        self.evaluator = None
        self.exporter = None
        self.version = self.config.fingerprint

    async def start(self):
        if self.problem:
            return
        try:
            path = self.ledger.path.parent / "quality.sqlite3"
            self.db = sqlite3.connect(path)
            path.chmod(0o600)
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS assessments (id TEXT PRIMARY KEY, turn_id TEXT, version TEXT, body TEXT)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS cursors (version TEXT PRIMARY KEY, baseline INTEGER, sequence INTEGER)"
            )
            sequence = max((e["sequence"] for e in self.ledger.events()), default=0)
            self.db.execute(
                "INSERT OR IGNORE INTO cursors VALUES (?, ?, ?)",
                (self.version, sequence, sequence),
            )
            self.db.commit()
            for job in self.jobs():
                if job["status"] == "running":
                    job.update(
                        status="error",
                        error="Interrupted assessment; provider usage may have occurred",
                    )
                if job["export_status"] == "sending":
                    job["export_status"] = "unknown"
                self.save(job)
            self.task = asyncio.create_task(self.run())
        except Exception as error:
            if self.db is not None:
                self.db.close()
                self.db = None
            self.problem = "Quality storage unavailable: " + type(error).__name__

    def jobs(self):
        if self.db is None:
            return []
        return sorted(
            (
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT body FROM assessments WHERE version=?", (self.version,)
                )
            ),
            key=lambda job: job["sequence"],
        )

    def save(self, job):
        job["updated_at"] = now()
        self.db.execute(
            "INSERT OR REPLACE INTO assessments VALUES (?, ?, ?, ?)",
            (
                job["id"],
                job["turn_id"],
                job["version"],
                json.dumps(job, allow_nan=False),
            ),
        )
        self.db.commit()

    def enqueue(self, finished, events):
        evidence = make_evidence(events, finished, self.config)
        job_id = digest([finished["turn_id"], self.version, evidence])
        if self.db.execute(
            "SELECT 1 FROM assessments WHERE id=?", (job_id,)
        ).fetchone():
            return False
        state = finished["payload"]["state"]
        reason = ""
        if state != "completed" or not evidence["answer"].strip():
            reason = "No completed delivered answer"
        if len(json.dumps(evidence, ensure_ascii=False)) > self.config.max_chars:
            reason = "Evidence exceeds configured limit; nothing was truncated or sent"
        job = {
            "id": job_id,
            "turn_id": finished["turn_id"],
            "conversation_id": finished["conversation_id"],
            "version": self.version,
            "judge_model": self.config.model,
            "sequence": finished["sequence"],
            "delivery_state": state,
            "executor": finished["payload"].get("executor", "none"),
            "status": "unassessable" if reason else "pending",
            "error": reason,
            "result": {},
            "created_at": now(),
            "export_status": "pending" if self.config.export_enabled else "disabled",
            "traces_sent": False,
            "attempts": [],
        }
        if "exceeds" in reason:
            job["export_status"] = "skipped"
        self.save(job)
        return True

    def discover(self, backfill=False):
        events = self.ledger.events()
        cursor = self.db.execute(
            "SELECT sequence FROM cursors WHERE version=?", (self.version,)
        ).fetchone()[0]
        count = 0
        for event in events:
            if event["type"] == "turn_finished" and (
                backfill or event["sequence"] > cursor
            ):
                count += self.enqueue(event, events)
        if events:
            self.db.execute(
                "UPDATE cursors SET sequence=? WHERE version=?",
                (events[-1]["sequence"], self.version),
            )
            self.db.commit()
        return count

    async def run(self):
        try:
            from .evaluator import Evaluator
            from .phoenix import PhoenixExport

            self.evaluator = await asyncio.to_thread(Evaluator, self.config)
            if self.config.export_enabled:
                self.exporter = PhoenixExport(self.config)
            while True:
                self.discover()
                job = next(
                    (
                        j
                        for j in self.jobs()
                        if j["status"] == "pending" or j["export_status"] == "pending"
                    ),
                    None,
                )
                if job:
                    await self.process(job)
                else:
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.problem = "Quality worker stopped: " + type(error).__name__

    async def process(self, job):
        events = self.ledger.events()
        finished = next(e for e in events if e["sequence"] == job["sequence"])
        evidence = make_evidence(events, finished, self.config)
        if job["status"] == "pending":
            job.update(status="running", error="")
            self.save(job)

            def checkpoint(result):
                job["result"] = result
                self.save(job)

            try:
                async with asyncio.timeout(240):
                    job["result"] = await self.evaluator.assess(
                        evidence, job["result"], checkpoint
                    )
                job["status"] = "scored"
            except asyncio.CancelledError:
                job.update(
                    status="error",
                    error="Assessment interrupted; provider usage may have occurred",
                )
                self.save(job)
                raise
            except Exception as error:
                job.update(
                    status="error", error="Assessment failed: " + type(error).__name__
                )
            self.save(job)
        if self.exporter and job["export_status"] == "pending":
            job["export_status"] = "sending"
            self.save(job)
            try:
                if not job["traces_sent"]:
                    await self.exporter.traces(events, finished, evidence)
                    job["traces_sent"] = True
                    self.save(job)
                await self.exporter.scores(job)
                job["export_status"] = "sent"
            except asyncio.CancelledError:
                job["export_status"] = "unknown"
                self.save(job)
                raise
            except Exception:
                job["export_status"] = "error" if job["traces_sent"] else "unknown"
            self.save(job)

    def retry(self, turn_id, allow_trace_resend=False):
        job = next((j for j in reversed(self.jobs()) if j["turn_id"] == turn_id), None)
        if job is None:
            raise NotFoundError("Assessment not found")
        job["attempts"].append(
            {
                "at": now(),
                "status": job["status"],
                "error": job["error"],
                "export_status": job["export_status"],
            }
        )
        if job["status"] == "error":
            job["status"] = "pending"
            if self.exporter:
                job["export_status"] = (
                    "pending"
                    if job["traces_sent"] or allow_trace_resend
                    else job["export_status"]
                )
        if job["export_status"] == "error" or (
            job["export_status"] == "unknown" and allow_trace_resend
        ):
            job["export_status"] = "pending"
        self.save(job)
        return self.summary(turn_id)

    def summary(self, turn_id):
        job = next((j for j in reversed(self.jobs()) if j["turn_id"] == turn_id), None)
        if job is None:
            if self.problem:
                return {
                    "status": "disabled" if not self.config.enabled else "unavailable",
                    "error": self.problem,
                }
            baseline = self.db.execute(
                "SELECT baseline FROM cursors WHERE version=?", (self.version,)
            ).fetchone()[0]
            eligible = any(
                e["sequence"] > baseline and e["type"] == "turn_finished"
                for e in self.ledger.events(turn_id=turn_id)
            )
            return {"status": "pending" if eligible else "not_assessed"}
        result = {k: v for k, v in job.items() if k not in {"attempts", "sequence"}}
        if self.problem and result["status"] == "pending":
            result.update(status="unavailable", error=self.problem)
            if result["export_status"] == "pending":
                result["export_status"] = "unavailable"
        return result

    def status(self):
        jobs = self.jobs()
        groups = {}
        for executor in {j.get("executor", "none") for j in jobs}:
            members = [j for j in jobs if j.get("executor", "none") == executor]
            verdicts = Counter(
                j["result"].get("rubric", {}).get("verdict", "not_scored")
                for j in members
            )
            assessed = verdicts["PASS"] + verdicts["FAIL"]
            groups[executor] = {
                "turns": len(members),
                "verdicts": dict(verdicts),
                "assessable_answers": assessed,
                "judge_pass_rate": verdicts["PASS"] / assessed if assessed else None,
            }
        return {
            "enabled": self.config.enabled,
            "ready": not self.problem,
            "problem": self.problem,
            "by_executor": groups,
            "version": self.version,
            "model": self.config.model,
            "counts": dict(Counter(j["status"] for j in jobs)),
            "export_counts": dict(Counter(j["export_status"] for j in jobs)),
            "delivery_counts": dict(Counter(j["delivery_state"] for j in jobs)),
            "dashboard_url": self.config.host if self.config.export_enabled else None,
        }

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        async with AsyncExitStack() as stack:
            if self.db:
                stack.callback(self.db.close)
            if self.exporter:
                stack.push_async_callback(self.exporter.close)
            if self.evaluator:
                stack.push_async_callback(self.evaluator.close)
