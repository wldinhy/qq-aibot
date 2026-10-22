from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Awaitable, Callable


class RequestQueue:
    """FIFO per conversation; ready conversations share a fixed worker pool."""

    def __init__(self, handler: Callable[[Any], Awaitable[None]], workers: int, tasks: set[asyncio.Task]) -> None:
        self.handler = handler
        self.workers = max(1, workers)
        self.tasks = tasks
        self.ready: asyncio.Queue[str] = asyncio.Queue()
        self.pending: dict[str, deque] = {}
        self.running: set[str] = set()
        self.started = False

    def submit(self, session_id: str, job: Any) -> bool:
        # A single worker uses one global queue, so serial mode is global FIFO.
        key = session_id if self.workers > 1 else "serial"
        waiting = key in self.pending or len(self.pending) >= self.workers
        if key not in self.pending:
            self.pending[key] = deque()
            self.ready.put_nowait(key)
        self.pending[key].append(job)
        if not self.started:
            self.started = True
            for _ in range(self.workers):
                task = asyncio.create_task(self._worker())
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        return waiting

    async def _worker(self) -> None:
        while True:
            key = await self.ready.get()
            self.running.add(key)
            job = self.pending[key].popleft()
            try:
                await self.handler(job)
            except Exception as exc:
                print(f"[queue] request failed: {type(exc).__name__}")
            finally:
                self.running.discard(key)
                if self.pending[key]:
                    self.ready.put_nowait(key)
                else:
                    del self.pending[key]
                self.ready.task_done()
