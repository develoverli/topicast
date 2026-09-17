from __future__ import annotations

import hashlib
import hmac
import json

from httpx import AsyncClient

from tests.conftest import FakeGateway, make_config
from topicast.config import GitHubHookConfig, HooksConfig
from topicast.runtime import Runtime
from topicast.security import ALL_ALIASES, Scope

PUSH = {
    "ref": "refs/heads/main",
    "pusher": {"name": "octocat"},
    "repository": {"full_name": "acme/app", "html_url": "https://github.com/acme/app"},
    "commits": [
        {"id": "abcdef1234", "message": "feat: ship it\n\nbody", "url": "https://github.com/c/1"}
    ],
}


async def _hook_key(runtime: Runtime, name: str = "hook") -> str:
    _, token = await runtime.keys.create(name, [Scope.HOOKS], [ALL_ALIASES])
    return token


async def test_generic_hook_renders_title_and_message(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    token = await _hook_key(runtime)
    response = await client.post(
        f"/v1/hooks/generic/alerts?token={token}",
        json={"title": "Backup", "message": "finished in 4m", "level": "success"},
    )
    assert response.status_code == 202, response.text
    final = await runtime.worker.wait_for(response.json()["id"], timeout=5)
    assert final is not None
    assert gateway.sent[0].text == "✅ <b>Backup</b>\nfinished in 4m"
    assert gateway.sent[0].parse_mode == "HTML"


async def test_generic_hook_uses_a_template(client: AsyncClient, runtime: Runtime) -> None:
    runtime.config = make_config(
        hooks=HooksConfig(templates={"plain": "Host {{ payload.host }} is {{ payload.state }}"})
    )
    runtime.service.config = runtime.config
    token = await _hook_key(runtime, "tpl")
    response = await client.post(
        f"/v1/hooks/generic/alerts?token={token}&template=plain&parse_mode=plain",
        json={"host": "nas", "state": "hot"},
    )
    assert response.status_code == 202
    final = await runtime.worker.wait_for(response.json()["id"], timeout=5)
    assert final is not None
    assert "Host nas is hot" in (final.payload["texts"][0])


async def test_unknown_template_is_404(client: AsyncClient, runtime: Runtime) -> None:
    token = await _hook_key(runtime, "missing-tpl")
    response = await client.post(
        f"/v1/hooks/generic/alerts?token={token}&template=nope", json={"a": 1}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_template"


async def test_hook_requires_the_hooks_scope(client: AsyncClient, runtime: Runtime) -> None:
    _, send_only = await runtime.keys.create("send-only-hook", [Scope.SEND], [ALL_ALIASES])
    response = await client.post(
        f"/v1/hooks/generic/alerts?token={send_only}", json={"message": "hi"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "missing_scope"


async def test_unknown_source_is_404(client: AsyncClient, runtime: Runtime) -> None:
    token = await _hook_key(runtime, "src")
    response = await client.post(f"/v1/hooks/nessus/alerts?token={token}", json={})
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_source"


async def test_github_push_is_rendered(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    token = await _hook_key(runtime, "gh")
    response = await client.post(
        f"/v1/hooks/github/deploys?token={token}",
        json=PUSH,
        headers={"X-GitHub-Event": "push"},
    )
    assert response.status_code == 202
    assert await runtime.worker.wait_for(response.json()["id"], timeout=5)
    text = gateway.sent[0].text or ""
    assert "octocat" in text
    assert "feat: ship it" in text
    assert "body" not in text  # only the subject line


async def test_github_signature_is_verified(client: AsyncClient, runtime: Runtime) -> None:
    secret = "s3cret"
    runtime.config = make_config(hooks=HooksConfig(github=GitHubHookConfig(secret=secret)))
    runtime.service.config = runtime.config
    token = await _hook_key(runtime, "gh-signed")
    body = json.dumps(PUSH).encode()
    headers = {"X-GitHub-Event": "push", "Content-Type": "application/json"}

    unsigned = await client.post(
        f"/v1/hooks/github/deploys?token={token}", content=body, headers=headers
    )
    assert unsigned.status_code == 401
    assert unsigned.json()["code"] == "invalid_signature"

    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    signed = await client.post(
        f"/v1/hooks/github/deploys?token={token}",
        content=body,
        headers={**headers, "X-Hub-Signature-256": f"sha256={signature}"},
    )
    assert signed.status_code == 202


async def test_github_ignores_unsupported_events(client: AsyncClient, runtime: Runtime) -> None:
    token = await _hook_key(runtime, "gh-ignore")
    response = await client.post(
        f"/v1/hooks/github/deploys?token={token}",
        json={"zen": "hi"},
        headers={"X-GitHub-Event": "star"},
    )
    assert response.status_code == 204


async def test_uptime_kuma_down_and_up(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    token = await _hook_key(runtime, "kuma")
    payload = {
        "heartbeat": {"status": 0, "msg": "connect ECONNREFUSED"},
        "monitor": {"id": 3, "name": "nas", "url": "https://nas.local"},
        "msg": "[nas] [DOWN]",
    }
    down = await client.post(f"/v1/hooks/uptime-kuma/alerts?token={token}", json=payload)
    assert down.status_code == 202
    assert await runtime.worker.wait_for(down.json()["id"], timeout=5)
    assert "is DOWN" in (gateway.sent[0].text or "")

    payload["heartbeat"] = {"status": 1, "msg": "200 - OK"}
    up = await client.post(f"/v1/hooks/uptime-kuma/alerts?token={token}", json=payload)
    assert await runtime.worker.wait_for(up.json()["id"], timeout=5)
    assert "is UP" in (gateway.sent[1].text or "")


async def test_uptime_kuma_repeats_are_deduplicated(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    token = await _hook_key(runtime, "kuma-dupe")
    payload = {
        "heartbeat": {"status": 0, "msg": "down"},
        "monitor": {"id": 9, "name": "db"},
        "msg": "down",
    }
    first = await client.post(f"/v1/hooks/uptime-kuma/alerts?token={token}", json=payload)
    await runtime.worker.wait_for(first.json()["id"], timeout=5)
    second = await client.post(f"/v1/hooks/uptime-kuma/alerts?token={token}", json=payload)
    assert second.json()["deduplicated"] is True
    assert len(gateway.sent) == 1


async def test_alertmanager_firing(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    token = await _hook_key(runtime, "am")
    payload = {
        "status": "firing",
        "commonLabels": {"alertname": "HighCPU", "severity": "critical"},
        "alerts": [
            {
                "status": "firing",
                "labels": {"instance": "web-1"},
                "annotations": {"summary": "CPU above 95%"},
            }
        ],
        "externalURL": "https://alerts.local",
    }
    response = await client.post(f"/v1/hooks/alertmanager/alerts?token={token}", json=payload)
    assert response.status_code == 202
    assert await runtime.worker.wait_for(response.json()["id"], timeout=5)
    text = gateway.sent[0].text or ""
    assert "[FIRING]" in text
    assert "CPU above 95%" in text
    assert "web-1" in text
    assert gateway.sent[0].silent is False  # critical is loud
