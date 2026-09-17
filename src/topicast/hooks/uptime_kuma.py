"""Uptime Kuma "Webhook" notification type (application/json body)."""

from __future__ import annotations

from html import escape
from typing import Any

from topicast.config import AppConfig, Level
from topicast.hooks import HookError, HookMessage, HookRequest

# heartbeat.status values used by Uptime Kuma
_DOWN, _UP, _PENDING, _MAINTENANCE = 0, 1, 2, 3


class UptimeKumaAdapter:
    name = "uptime-kuma"

    def verify(self, body: bytes, request: HookRequest, config: AppConfig) -> None:
        return None

    def render(self, payload: Any, request: HookRequest, config: AppConfig) -> HookMessage:
        if not isinstance(payload, dict):
            raise HookError(422, "invalid_payload", "Expected a JSON object.")
        monitor = payload.get("monitor") or {}
        heartbeat = payload.get("heartbeat")
        msg = str(payload.get("msg") or "")
        if not heartbeat:
            return HookMessage(text=f"🔔 Uptime Kuma: {escape(msg, quote=False)}", level=Level.INFO)

        status = heartbeat.get("status")
        name = escape(str(monitor.get("name", "monitor")), quote=False)
        detail = escape(str(heartbeat.get("msg") or msg), quote=False)
        if status == _DOWN:
            level, headline = Level.ERROR, f"<b>{name}</b> is DOWN"
        elif status == _UP:
            level, headline = Level.SUCCESS, f"<b>{name}</b> is UP"
        elif status == _MAINTENANCE:
            level, headline = Level.INFO, f"<b>{name}</b> is in maintenance"
        else:
            level, headline = Level.WARNING, f"<b>{name}</b> is pending"
        lines = [headline]
        url = monitor.get("url")
        if url and url != "https://":
            lines.append(escape(str(url), quote=False))
        if detail:
            lines.append(f"<i>{detail}</i>")
        # Same monitor + same status within the dedupe window collapses into one message.
        return HookMessage(
            text="\n".join(lines),
            level=level,
            dedupe_key=f"uptime-kuma:{monitor.get('id', name)}:{status}",
        )
