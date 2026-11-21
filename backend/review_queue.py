import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from itertools import count
import logging


logger = logging.getLogger(__name__)
ReviewJob = Callable[[], Awaitable[None]]


class ReviewQueue:
    """Dispatch saved reviews on the hosted process's event loop."""

    def __init__(self, max_workers: int):
        if max_workers < 1:
            raise ValueError("At least one review worker is required.")
        self.max_workers = max_workers
        self._pending: asyncio.PriorityQueue[tuple[int, int, object, ReviewJob]] = asyncio.PriorityQueue()
        self._jobs: Counter[object] = Counter()
        self._last_order = 0
        self._tie_breakers = count()
        self._workers: list[asyncio.Task] = []

    def next_order(self) -> int:
        self._last_order += 1
        return self._last_order

    def enqueue(self, order: int, workspace: object, run: ReviewJob) -> None:
        # Startup registers all durable queued jobs before starting dispatch.
        self._last_order = max(self._last_order, order)
        self._pending.put_nowait((order, next(self._tie_breakers), workspace, run))
        self._jobs[workspace] += 1

    def busy(self, workflow: object) -> bool:
        return self._jobs[workflow] > 0

    def start(self) -> None:
        if not self._workers:
            self._workers = [asyncio.create_task(self._worker()) for _ in range(self.max_workers)]

    async def close(self) -> None:
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        while not self._pending.empty():
            self._pending.get_nowait()
            self._pending.task_done()
        self._jobs.clear()

    async def _worker(self) -> None:
        while True:
            _, _, workspace, run = await self._pending.get()
            try:
                await run()
            except Exception as error:
                logger.error("A review queue job could not be processed (%s).", type(error).__name__)
            finally:
                self._jobs[workspace] -= 1
                if self._jobs[workspace] == 0:
                    del self._jobs[workspace]
                self._pending.task_done()
