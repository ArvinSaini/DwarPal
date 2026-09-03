import pytest

from agentgate.db import connect, init_db


class Clock:
    """Deterministic clock for tests. Default: 2026-09-03T12:26:40Z."""

    def __init__(self, now: int = 1_756_900_000):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def tick(self, seconds: int = 1) -> int:
        self.now += seconds
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()
