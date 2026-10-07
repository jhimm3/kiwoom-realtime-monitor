"""Instance-local clock for correctness gates, never a performance clock."""
import asyncio
from datetime import timedelta


class CollectorTestClock:
    correctness_only = True

    def __init__(self, origin):
        self.origin = origin
        self.offset = 0.0
        self.idle = asyncio.Event()
        self.waiter = None
        self.due = None

    def elapsed(self):
        return self.offset

    def now(self):
        return self.origin + timedelta(seconds=self.offset)

    async def sleep(self, seconds):
        self.due = self.offset + seconds
        self.waiter = asyncio.get_running_loop().create_future()
        self.idle.set()
        try:
            await self.waiter
        finally:
            self.idle.clear()

    async def wait_until(self, offset, stop):
        while self.offset < offset and not stop.is_set():
            await asyncio.wait_for(self.idle.wait(), 15)
            self.offset = min(offset, self.due)
            if self.due <= self.offset:
                future = self.waiter
                self.idle.clear()
                future.set_result(None)
                # Wait for the real persistence loop, including its to_thread
                # writes, to complete before advancing the correctness clock.
                await asyncio.wait_for(self.idle.wait(), 15)
