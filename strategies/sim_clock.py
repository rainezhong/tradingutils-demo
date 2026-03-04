"""Simulated clock and async primitives for replay/testing.

Provides drop-in replacements for time.time, asyncio.sleep, and
asyncio.wait_for that operate on simulated time controlled externally
(e.g. by advancing to each recorded frame's timestamp).
"""

import asyncio
from typing import Callable


class SimulatedClock:
    """Clock whose time advances under external control.

    Drop-in replacement for ``time.time`` — call the instance to read the
    current simulated time.
    """

    def __init__(self, start_time: float = 0.0) -> None:
        self._now = start_time

    def __call__(self) -> float:
        return self._now

    def advance_to(self, t: float) -> None:
        """Advance clock to *t*.  Never goes backward."""
        if t > self._now:
            self._now = t


async def sim_sleep(seconds: float) -> None:
    """Yield without real delay — ``await asyncio.sleep(0)``."""
    await asyncio.sleep(0)


def make_sim_wait_for_event(clock: SimulatedClock) -> Callable:
    """Return an async ``(event, timeout) -> bool`` that uses *clock*.

    Loops cooperatively:
      - ``event.is_set()`` → return ``True``
      - ``clock() >= deadline`` → return ``False`` (timeout)
      - else yield via ``asyncio.sleep(0)``
    """

    async def _sim_wait(event: asyncio.Event, timeout: float) -> bool:
        deadline = clock() + timeout
        while True:
            if event.is_set():
                return True
            if clock() >= deadline:
                return False
            await asyncio.sleep(0)

    return _sim_wait
