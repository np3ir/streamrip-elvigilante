import asyncio

import pytest

from streamrip.client.request_budget import SharedRequestBudget


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.waits = []

    def now(self):
        return self.value

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += seconds
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_shared_budget_spaces_concurrent_callers_without_initial_wait():
    clock = FakeClock()
    budget = SharedRequestBudget(
        60,
        clock=clock.now,
        sleeper=clock.sleep,
        jitter=lambda: 0.0,
    )

    await asyncio.gather(*(budget.acquire() for _ in range(3)))

    assert clock.waits == [1.0, 1.0]
    assert budget.request_count == 3


@pytest.mark.asyncio
async def test_nonpositive_rpm_uses_safe_default():
    budget = SharedRequestBudget(0, jitter=lambda: 0.0)

    assert budget.interval == 1.0
