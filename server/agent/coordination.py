from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis


class LeaseLost(RuntimeError):
    pass


class ConversationLease:
    def __init__(self, redis: Redis, conversation_id: str, ttl_ms: int = 30_000):
        if ttl_ms < 3000:
            raise ValueError("Lease TTL is too short")
        key = (
            "edgedispatch:conversation:"
            + hashlib.sha256(conversation_id.encode()).hexdigest()
        )
        self._lock = redis.lock(
            key, timeout=ttl_ms / 1000, blocking=False, thread_local=False
        )
        self.ttl_ms = ttl_ms
        self.lost = asyncio.Event()
        self._renew_task: asyncio.Task | None = None

    async def acquire(self) -> None:
        if self._renew_task is not None or self.lost.is_set():
            raise LeaseLost("Use a new lease for each execution")
        try:
            acquired = await self._lock.acquire()
        except Exception:
            raise LeaseLost("Redis unavailable; execution cannot start") from None
        if not acquired:
            raise LeaseLost("Conversation is already owned")
        self._renew_task = asyncio.create_task(self._renew())

    async def _renew(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.ttl_ms / 3000)
                await self._lock.reacquire()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.lost.set()

    async def ensure_owner(self) -> None:
        if self._renew_task is None or self.lost.is_set():
            raise LeaseLost("Conversation lease lost; no further calls permitted")
        try:
            owned = await self._lock.owned()
        except Exception:
            self.lost.set()
            raise LeaseLost("Redis unavailable; no further calls permitted") from None
        if not owned:
            self.lost.set()
            raise LeaseLost("Conversation lease lost; no further calls permitted")

    async def close(self) -> None:
        self.lost.set()
        if self._renew_task is None:
            return
        self._renew_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._renew_task
        self._renew_task = None
        with suppress(Exception):
            await self._lock.release()
