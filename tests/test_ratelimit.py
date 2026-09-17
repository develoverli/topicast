from __future__ import annotations

from topicast.delivery.ratelimit import TokenBucket


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_bucket_spaces_out_calls() -> None:
    clock = Clock()
    bucket = TokenBucket(20, 60, clock=clock)  # 20 per minute
    assert bucket.delay() == 0.0
    bucket._tokens -= 1  # consume the only token
    assert bucket.delay() > 2.9  # ~3s until the next one
    clock.now += 3
    assert bucket.delay() == 0.0


def test_pause_blocks_the_bucket() -> None:
    clock = Clock()
    bucket = TokenBucket(30, 1, clock=clock)
    bucket.pause(5)
    assert bucket.delay() == 5
    clock.now += 5
    assert bucket.delay() == 0.0


def test_burst_is_capped_by_capacity() -> None:
    clock = Clock()
    bucket = TokenBucket(30, 1, clock=clock)
    clock.now += 100  # idle for a long time
    bucket._refill(clock.now)
    assert bucket._tokens == 30
