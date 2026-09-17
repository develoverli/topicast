"""Source name → adapter."""

from __future__ import annotations

from topicast.hooks import HookAdapter
from topicast.hooks.alertmanager import AlertmanagerAdapter
from topicast.hooks.generic import GenericAdapter
from topicast.hooks.github import GitHubAdapter
from topicast.hooks.uptime_kuma import UptimeKumaAdapter

_alertmanager = AlertmanagerAdapter()

ADAPTERS: dict[str, HookAdapter] = {
    "generic": GenericAdapter(),
    "github": GitHubAdapter(),
    "uptime-kuma": UptimeKumaAdapter(),
    "alertmanager": _alertmanager,
    "grafana": _alertmanager,
}
