from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from tests.conftest import FakeGateway
from topicast.db import MessageStatus
from topicast.runtime import Runtime


def in_hours(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


async def test_scheduled_message_is_not_sent_yet(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    response = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "good morning", "send_at": in_hours(5)}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == MessageStatus.QUEUED
    assert body["scheduled_for"] is not None
    assert gateway.sent == []

    stored = await runtime.service.get(body["id"])
    assert stored is not None
    assert stored.next_attempt_at > stored.created_at


async def test_wait_does_not_block_on_a_scheduled_message(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages?wait=true",
        json={"to": "deploys", "text": "later", "send_at": in_hours(2)},
    )
    assert response.status_code == 202
    assert response.json()["scheduled_for"] is not None


async def test_due_schedule_is_delivered(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    past_due = (datetime.now(UTC) + timedelta(seconds=0.2)).isoformat()
    response = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "now-ish", "send_at": past_due}
    )
    message_id = response.json()["id"]
    final = await runtime.worker.wait_for(message_id, timeout=5)
    assert final is not None
    assert final.status == MessageStatus.DELIVERED
    assert gateway.sent[0].text == "now-ish"


async def test_scheduled_message_can_be_cancelled(
    client: AsyncClient, gateway: FakeGateway
) -> None:
    created = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "cancel me", "send_at": in_hours(3)}
    )
    deleted = await client.delete(f"/v1/messages/{created.json()['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == MessageStatus.DELETED
    assert gateway.sent == []
    assert gateway.deleted == []  # nothing had been posted to Telegram


async def test_past_times_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "too late", "send_at": in_hours(-2)}
    )
    assert response.status_code == 422
    assert "past" in response.text


async def test_naive_times_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages",
        json={"to": "deploys", "text": "no zone", "send_at": "2030-01-01T09:00:00"},
    )
    assert response.status_code == 422
    assert "timezone" in response.text


async def test_far_future_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages",
        json={"to": "deploys", "text": "someday", "send_at": in_hours(24 * 400)},
    )
    assert response.status_code == 422
    assert "365 days" in response.text


async def test_immediate_messages_have_no_schedule(client: AsyncClient) -> None:
    response = await client.post("/v1/messages", json={"to": "deploys", "text": "now"})
    assert response.json()["scheduled_for"] is None


async def test_same_idempotency_key_with_a_new_time_conflicts(client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "schedule-1"}
    body = {"to": "deploys", "text": "report", "send_at": in_hours(4)}
    first = await client.post("/v1/messages", json=body, headers=headers)
    assert first.status_code == 202

    moved = await client.post(
        "/v1/messages", json={**body, "send_at": in_hours(6)}, headers=headers
    )
    assert moved.status_code == 409
    assert moved.json()["code"] == "idempotency_conflict"
