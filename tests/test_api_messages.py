from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import FakeGateway
from topicast.delivery.formatting import TEXT_LIMIT
from topicast.delivery.telegram import DeliveryError
from topicast.runtime import Runtime
from topicast.security import ALL_ALIASES, Scope


async def test_send_waits_for_delivery(client: AsyncClient, gateway: FakeGateway) -> None:
    response = await client.post(
        "/v1/messages?wait=true", json={"to": "alerts", "text": "hello", "level": "success"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "delivered"
    assert body["telegram_message_ids"]
    sent = gateway.sent[0]
    assert sent.text == "✅ hello"
    assert sent.silent is True  # success is silent by default
    assert sent.target.thread_id == 5


async def test_send_without_wait_is_accepted(client: AsyncClient) -> None:
    response = await client.post("/v1/messages", json={"to": "deploys", "text": "queued"})
    assert response.status_code == 202
    assert response.json()["status"] in {"queued", "sending", "delivered"}


async def test_unknown_alias_is_404(client: AsyncClient) -> None:
    response = await client.post("/v1/messages", json={"to": "nope", "text": "hi"})
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_alias"
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_missing_key_is_401(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages", json={"to": "alerts", "text": "hi"}, headers={"Authorization": ""}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "missing_api_key"


async def test_scope_and_alias_are_enforced(client: AsyncClient, runtime: Runtime) -> None:
    _, token = await runtime.keys.create("limited", [Scope.SEND], ["deploys"])
    headers = {"Authorization": f"Bearer {token}"}

    allowed = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "ok"}, headers=headers
    )
    assert allowed.status_code == 202

    denied = await client.post("/v1/messages", json={"to": "alerts", "text": "no"}, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "alias_not_allowed"

    no_edit = await client.delete(f"/v1/messages/{allowed.json()['id']}", headers=headers)
    assert no_edit.status_code == 403
    assert no_edit.json()["code"] == "missing_scope"


async def test_revoked_key_stops_working(client: AsyncClient, runtime: Runtime) -> None:
    _, token = await runtime.keys.create("temp", [Scope.SEND], [ALL_ALIASES])
    await runtime.keys.revoke("temp")
    response = await client.post(
        "/v1/messages",
        json={"to": "alerts", "text": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


async def test_long_text_is_split(client: AsyncClient, gateway: FakeGateway) -> None:
    text = "\n\n".join(["x" * 3000] * 2)
    response = await client.post("/v1/messages?wait=true", json={"to": "alerts", "text": text})
    assert response.status_code == 200
    assert len(gateway.sent) == 2
    assert len(response.json()["telegram_message_ids"]) == 2


async def test_long_text_can_be_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages",
        json={"to": "alerts", "text": "y" * (TEXT_LIMIT + 1), "on_overflow": "reject"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "text_too_long"


async def test_markup_fallback_reports_the_reason(
    client: AsyncClient, gateway: FakeGateway
) -> None:
    gateway.errors.append(DeliveryError("Can't parse entities", parse_error=True))
    response = await client.post(
        "/v1/messages?wait=true",
        json={"to": "alerts", "text": "*broken", "parse_mode": "MarkdownV2"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "delivered"
    assert "parse entities" in body["fallback_reason"]
    assert gateway.sent[0].parse_mode is None  # retried as plain text


async def test_permanent_failure_returns_502_with_wait(
    client: AsyncClient, gateway: FakeGateway
) -> None:
    gateway.errors.append(DeliveryError("chat not found"))
    response = await client.post("/v1/messages?wait=true", json={"to": "alerts", "text": "hi"})
    assert response.status_code == 502
    assert response.json()["code"] == "delivery_failed"


async def test_deduplication_collapses_repeats(client: AsyncClient, gateway: FakeGateway) -> None:
    first = await client.post("/v1/messages?wait=true", json={"to": "alerts", "text": "same"})
    second = await client.post("/v1/messages", json={"to": "alerts", "text": "same"})
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    assert second.json()["id"] == first.json()["id"]
    assert len(gateway.sent) == 1


async def test_dedupe_window_zero_disables_it(client: AsyncClient, gateway: FakeGateway) -> None:
    await client.post("/v1/messages?wait=true", json={"to": "deploys", "text": "again"})
    await client.post("/v1/messages?wait=true", json={"to": "deploys", "text": "again"})
    assert len(gateway.sent) == 2


async def test_idempotency_key_replays_the_same_message(
    client: AsyncClient, gateway: FakeGateway
) -> None:
    headers = {"Idempotency-Key": "abc-123"}
    first = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "once"}, headers=headers
    )
    second = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "once"}, headers=headers
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]

    conflict = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "different"}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


async def test_edit_and_delete(client: AsyncClient, gateway: FakeGateway) -> None:
    created = await client.post("/v1/messages?wait=true", json={"to": "alerts", "text": "first"})
    message_id = created.json()["id"]

    edited = await client.patch(f"/v1/messages/{message_id}", json={"text": "second"})
    assert edited.status_code == 200
    assert gateway.edits[0][1] == "second"

    deleted = await client.delete(f"/v1/messages/{message_id}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert gateway.deleted

    missing = await client.get("/v1/messages/does-not-exist")
    assert missing.status_code == 404


async def test_edit_requires_a_delivered_message(client: AsyncClient) -> None:
    queued = await client.post("/v1/messages", json={"to": "deploys", "text": "wait"})
    response = await client.patch(f"/v1/messages/{queued.json()['id']}", json={"text": "too soon"})
    assert response.status_code in {409, 200}


async def test_upload_sends_a_document(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    response = await client.post(
        "/v1/messages?wait=true",
        data={"to": "alerts", "text": "logs attached"},
        files={"file": ("app.log", b"log line\n", "text/plain")},
    )
    assert response.status_code == 200, response.text
    sent = gateway.sent[0]
    assert sent.kind == "document"
    assert sent.media[0].filename == "app.log"
    assert sent.text == "logs attached"
    # the spooled file is removed after delivery
    assert not list(runtime.settings.spool_dir.iterdir())


async def test_health_and_readiness(client: AsyncClient) -> None:
    assert (await client.get("/healthz")).json()["status"] == "ok"
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


async def test_metrics_are_exposed(client: AsyncClient) -> None:
    await client.post("/v1/messages?wait=true", json={"to": "alerts", "text": "metric"})
    body = (await client.get("/metrics")).text
    assert "topicast_messages_delivered_total" in body
