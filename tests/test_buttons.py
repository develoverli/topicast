from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import FakeGateway
from topicast.runtime import Runtime

DASHBOARD = {"text": "Open dashboard", "url": "https://grafana.local/d/abc"}
RUNBOOK = {"text": "Runbook", "url": "https://wiki.local/runbook"}


async def test_flat_list_becomes_one_row(client: AsyncClient, gateway: FakeGateway) -> None:
    response = await client.post(
        "/v1/messages?wait=true",
        json={"to": "alerts", "text": "Disk at 91%", "buttons": [DASHBOARD, RUNBOOK]},
    )
    assert response.status_code == 200, response.text
    keyboard = gateway.sent[0].keyboard
    assert keyboard is not None
    assert len(keyboard) == 1
    assert [b.text for b in keyboard[0]] == ["Open dashboard", "Runbook"]
    assert keyboard[0][0].url == DASHBOARD["url"]


async def test_nested_lists_become_rows(client: AsyncClient, gateway: FakeGateway) -> None:
    response = await client.post(
        "/v1/messages?wait=true",
        json={"to": "alerts", "text": "Deploy failed", "buttons": [[DASHBOARD], [RUNBOOK]]},
    )
    assert response.status_code == 200
    keyboard = gateway.sent[0].keyboard
    assert keyboard is not None
    assert len(keyboard) == 2


async def test_no_buttons_sends_none(client: AsyncClient, gateway: FakeGateway) -> None:
    await client.post("/v1/messages?wait=true", json={"to": "alerts", "text": "plain"})
    assert gateway.sent[0].keyboard is None


async def test_buttons_ride_on_the_last_chunk(client: AsyncClient, gateway: FakeGateway) -> None:
    text = "\n\n".join(["x" * 3000] * 2)
    await client.post(
        "/v1/messages?wait=true", json={"to": "alerts", "text": text, "buttons": [DASHBOARD]}
    )
    assert len(gateway.sent) == 2
    assert gateway.sent[0].keyboard is None
    assert gateway.sent[1].keyboard is not None


async def test_buttons_survive_a_restart_of_the_delivery(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    created = await client.post(
        "/v1/messages", json={"to": "deploys", "text": "queued", "buttons": [DASHBOARD]}
    )
    stored = await runtime.service.get(created.json()["id"])
    assert stored is not None
    # The keyboard lives in the payload, so a requeue after a crash keeps it.
    assert stored.payload["buttons"] == [[DASHBOARD]]


async def test_media_keeps_the_buttons(client: AsyncClient, gateway: FakeGateway) -> None:
    response = await client.post(
        "/v1/messages?wait=true",
        json={
            "to": "alerts",
            "text": "Report attached",
            "media": [{"type": "document", "url": "https://files.local/report.pdf"}],
            "buttons": [DASHBOARD],
        },
    )
    assert response.status_code == 200, response.text
    assert gateway.sent[0].kind == "document"
    assert gateway.sent[0].keyboard is not None


async def test_invalid_url_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages",
        json={"to": "alerts", "text": "hi", "buttons": [{"text": "bad", "url": "javascript:x"}]},
    )
    assert response.status_code == 422
    assert "http://" in response.text


async def test_too_many_rows_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages",
        json={"to": "alerts", "text": "hi", "buttons": [[DASHBOARD] for _ in range(9)]},
    )
    assert response.status_code == 422
    assert "rows" in response.text


async def test_too_many_buttons_per_row_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/messages",
        json={"to": "alerts", "text": "hi", "buttons": [DASHBOARD, RUNBOOK, DASHBOARD, RUNBOOK]},
    )
    assert response.status_code == 422
    assert "per row" in response.text
