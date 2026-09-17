# topicast

**Self-hosted notification hub for Telegram forum topics.**

One HTTP endpoint for every service you run. Messages are queued, rate limited, deduplicated
and routed to the right topic by name.

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Authorization: Bearer $TOPICAST_KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "alerts", "text": "Disk at 91%", "level": "warning"}'
```

## Why it exists

A Telegram group with **Topics** enabled is a great notification hub: one group, one topic per
concern. Wiring every service to the Bot API directly means copying the bot token everywhere,
hardcoding chat and thread ids, and rediscovering Telegram's flood limits in production.

topicast puts one small service in front of that:

| Without topicast | With topicast |
|---|---|
| Bot token in every service | One key per service, scoped to the aliases it may use |
| `chat_id` and `message_thread_id` in code | `"to": "alerts"` |
| Bursts dropped by flood limits | Queued, rate limited, retried with backoff |
| A flapping monitor floods the group | Dedupe window collapses repeats into `🔁 Repeated 42×` |
| Broken Markdown loses the message | Automatic plain-text fallback, reported in the response |

## How it works

```
your services ──► POST /v1/messages ──► SQLite queue ──► worker ──► Telegram
webhooks      ──► POST /v1/hooks/...        │              │
                                            │              ├─ token bucket per bot and per chat
                                            │              ├─ retry with backoff, honours 429
                                            │              └─ markup fallback, message splitting
                                            └─ dedupe window, idempotency keys
```

Everything lives in one container plus one SQLite file. No Redis, no Postgres, no queue broker.

## Where to go next

- [Quickstart](quickstart.md) — running in three commands.
- [Telegram setup](telegram-setup.md) — bot, group, topics and the ids you need.
- [Configuration](configuration.md) — aliases, levels, dedupe, rate limits.
- [API reference](api.md) — endpoints, fields, status codes, errors.
- [Webhooks](webhooks.md) — GitHub, Uptime Kuma, Alertmanager, Grafana, Slack- and
  Discord-compatible endpoints, generic.
- [Deployment](deployment.md) — Compose, reverse proxy, Tailscale, Kubernetes.
- [Deploying on Dokploy](dokploy.md) — the full path, click by click.
- [Security](security.md) — keys, scopes, exposure, hardening.
