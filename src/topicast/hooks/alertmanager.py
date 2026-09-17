"""Prometheus Alertmanager and Grafana unified alerting webhooks (same payload shape)."""

from __future__ import annotations

from html import escape
from typing import Any

from topicast.config import AppConfig, Level
from topicast.hooks import HookError, HookMessage, HookRequest

_MAX_ALERTS = 10
_SEVERITY_LEVELS = {
    "critical": Level.CRITICAL,
    "page": Level.CRITICAL,
    "error": Level.ERROR,
    "high": Level.ERROR,
    "warning": Level.WARNING,
    "warn": Level.WARNING,
    "info": Level.INFO,
    "none": Level.INFO,
}


def _e(value: object) -> str:
    return escape(str(value), quote=False)


class AlertmanagerAdapter:
    name = "alertmanager"

    def verify(self, body: bytes, request: HookRequest, config: AppConfig) -> None:
        return None

    def render(self, payload: Any, request: HookRequest, config: AppConfig) -> HookMessage:
        if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
            raise HookError(422, "invalid_payload", "Expected an Alertmanager payload.")
        alerts: list[dict[str, Any]] = payload["alerts"]
        status = str(payload.get("status", "firing"))
        common = payload.get("commonLabels") or {}
        group = payload.get("title") or common.get("alertname") or "Alert"

        if status == "resolved":
            level = Level.SUCCESS
        else:
            severity = str(common.get("severity", "")).lower()
            level = _SEVERITY_LEVELS.get(severity, Level.ERROR)

        lines = [f"<b>[{_e(status.upper())}]</b> {_e(group)} ({len(alerts)})"]
        for alert in alerts[:_MAX_ALERTS]:
            labels = alert.get("labels") or {}
            annotations = alert.get("annotations") or {}
            summary = (
                annotations.get("summary")
                or annotations.get("description")
                or labels.get("alertname", "")
            )
            where = labels.get("instance") or labels.get("job") or ""
            marker = "✅" if alert.get("status") == "resolved" else "🔥"
            suffix = f" <code>{_e(where)}</code>" if where else ""
            lines.append(f"{marker} {_e(summary)}{suffix}")
        if len(alerts) > _MAX_ALERTS:
            lines.append(f"…and {len(alerts) - _MAX_ALERTS} more")
        external = payload.get("externalURL")
        if external:
            lines.append(f'<a href="{escape(str(external), quote=True)}">Open</a>')

        group_key = payload.get("groupKey") or group
        return HookMessage(
            text="\n".join(lines),
            level=level,
            dedupe_key=f"alertmanager:{group_key}:{status}:{len(alerts)}",
        )
