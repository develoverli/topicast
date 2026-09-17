from __future__ import annotations

import asyncio

import pytest

from tests.conftest import FakeGateway
from topicast.db import MessageStatus
from topicast.delivery import worker as worker_module
from topicast.delivery.service import OutgoingMessage
from topicast.delivery.telegram import DeliveryError
from topicast.runtime import Runtime


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_module, "backoff_delay", lambda attempt: 0.01)


async def _send(runtime: Runtime, text: str, alias: str = "deploys") -> str:
    result = await runtime.service.enqueue(OutgoingMessage(alias=alias, text=text), key_id=None)
    return result.message.id


async def test_transient_errors_are_retried(runtime: Runtime, gateway: FakeGateway) -> None:
    gateway.errors.extend([DeliveryError("boom", retryable=True)] * 2)
    message_id = await _send(runtime, "flaky")
    final = await runtime.worker.wait_for(message_id, timeout=5)
    assert final is not None
    assert final.status == MessageStatus.DELIVERED
    assert final.attempts == 2
    assert len(gateway.sent) == 1


async def test_retries_stop_at_max_attempts(runtime: Runtime, gateway: FakeGateway) -> None:
    gateway.errors.extend([DeliveryError("down", retryable=True)] * 10)
    message_id = await _send(runtime, "never")
    final = await runtime.worker.wait_for(message_id, timeout=5)
    assert final is not None
    assert final.status == MessageStatus.FAILED
    assert final.attempts == runtime.settings.max_attempts
    assert "down" in (final.last_error or "")


async def test_rate_limit_pauses_and_retries(runtime: Runtime, gateway: FakeGateway) -> None:
    gateway.errors.append(DeliveryError("flood", retryable=True, retry_after=0.05))
    message_id = await _send(runtime, "flooded")
    final = await runtime.worker.wait_for(message_id, timeout=5)
    assert final is not None
    assert final.status == MessageStatus.DELIVERED
    assert final.attempts == 0  # flood control does not burn an attempt


async def test_failed_message_can_be_retried_by_hand(
    runtime: Runtime, gateway: FakeGateway
) -> None:
    gateway.errors.extend([DeliveryError("nope", retryable=True)] * 10)
    message_id = await _send(runtime, "manual")
    assert (await runtime.worker.wait_for(message_id, timeout=5)) is not None

    gateway.errors.clear()  # Telegram is healthy again
    await runtime.service.retry(message_id)
    final = await runtime.worker.wait_for(message_id, timeout=5)
    assert final is not None
    assert final.status == MessageStatus.DELIVERED


async def test_dedupe_window_sends_a_summary(runtime: Runtime, gateway: FakeGateway) -> None:
    # The "alerts" alias uses the default 60s dedupe window.
    first = await runtime.service.enqueue(
        OutgoingMessage(alias="alerts", text="repeating"), key_id=None
    )
    for _ in range(3):
        await runtime.service.enqueue(
            OutgoingMessage(alias="alerts", text="repeating"), key_id=None
        )
    assert (await runtime.worker.wait_for(first.message.id, timeout=5)) is not None

    # Expire the window, then let housekeeping close it.
    async with runtime.db.session() as session:
        from sqlalchemy import update

        from topicast.db import DedupeWindow
        from topicast.db.models import utcnow

        await session.execute(update(DedupeWindow).values(expires_at=utcnow().replace(year=2000)))
        await session.commit()

    assert await runtime.service.flush_dedupe_windows() == 1
    for _ in range(50):
        if len(gateway.sent) > 1:
            break
        await asyncio.sleep(0.05)
    assert "Repeated 3×" in (gateway.sent[1].text or "")


async def test_queued_message_survives_a_restart(runtime: Runtime, gateway: FakeGateway) -> None:
    gateway.errors.append(DeliveryError("hiccup", retryable=True))
    message_id = await _send(runtime, "resilient")
    final = await runtime.worker.wait_for(message_id, timeout=5)
    assert final is not None and final.status == MessageStatus.DELIVERED
