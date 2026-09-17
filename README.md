<div align="center">

# topicast

**Self-hosted notification hub for Telegram forum topics.**

One HTTP endpoint for every service you run. Queued, rate limited, deduplicated,
and routed to the right topic by name.

[![CI](https://github.com/develoverli/topicast/actions/workflows/ci.yml/badge.svg)](https://github.com/develoverli/topicast/actions/workflows/ci.yml)
[![Docker image](https://img.shields.io/badge/ghcr.io-develoverli%2Ftopicast-blue?logo=docker)](https://github.com/develoverli/topicast/pkgs/container/topicast)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Documentation](https://develoverli.github.io/topicast/) ·
[Quickstart](#quickstart) ·
[Configuration](https://develoverli.github.io/topicast/configuration/) ·
[API reference](https://develoverli.github.io/topicast/api/)

</div>

---

Telegram groups with **topics** (forum mode) make a great notification hub: one group,
one topic per concern — alerts, deploys, backups, finance. topicast puts an HTTP API in
front of it so your apps never deal with bot tokens, chat ids, flood limits or Markdown
escaping again.

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Authorization: Bearer $TOPICAST_KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "alerts", "text": "Disk at 91%", "level": "warning"}'
```

## Why

| Without topicast | With topicast |
|---|---|
| Bot token copied into every service | One key per service, scoped to the aliases it may use |
| Magic numbers (`chat_id`, `message_thread_id`) in code | Names: `"to": "alerts"` |
| Bursts silently dropped by Telegram's flood limits | Queued, rate limited and retried with backoff |
| A repeated alert floods the group | Dedupe window collapses it into `🔁 Repeated 42×` |
| Broken Markdown loses the message | Automatic fallback to plain text, reported in the response |
| Every service writes its own Telegram client | One endpoint, OpenAPI docs, `curl`-friendly |

## Features

- **Aliases** — `alerts`, `deploys`, `backups` map to a chat plus a topic in `config.yaml`.
- **Queue with rate limiting** — per-bot and per-chat token buckets stay under Telegram's
  limits; `429` responses from Telegram pause the chat and retry automatically.
- **Deduplication** — identical alerts inside a window are suppressed and summarised.
- **Idempotency** — retry a request with the same `Idempotency-Key` without double posting.
- **Levels** — `info`, `success`, `warning`, `error`, `critical` add an emoji prefix and pick
  the notification sound.
- **Quiet hours** — silence an alias between, say, 23:00 and 08:00, while `critical` still
  rings through.
- **Long messages** — split on paragraph boundaries, or truncated, or rejected. Your call.
- **Files** — upload photos and documents, or point at a URL; send albums of up to 10 items.
- **Edit and delete** — `PATCH` and `DELETE` a message you already sent.
- **Webhooks in** — GitHub (signature-verified), Uptime Kuma, Alertmanager/Grafana, and a
  generic endpoint with your own Jinja2 template.
- **Slack and Discord compatible** — point any tool that only speaks "Slack webhook" or
  "Discord webhook" (Watchtower, Portainer, Sonarr, Netdata, Gitea…) at topicast and it posts
  to Telegram instead.
- **Operable** — `/healthz`, `/readyz`, Prometheus `/metrics`, JSON logs, SQLite storage, and
  a `topicast doctor` that checks your token, chat, permissions and topics against Telegram.

## Quickstart

Requires Docker. Three steps:

```bash
# 1. Get the compose file and the config template
mkdir topicast && cd topicast
curl -O https://raw.githubusercontent.com/develoverli/topicast/main/compose.yaml
curl -o config.yaml https://raw.githubusercontent.com/develoverli/topicast/main/config.example.yaml

# 2. Fill in the secrets
cat > .env <<'EOF'
TOPICAST_SECRET_KEY=replace-with-32-plus-random-characters
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=-1001234567890
EOF

# 3. Start it, then mint a key for your first service
docker compose up -d
docker compose exec topicast topicast keys create my-service --scope send --alias alerts
```

Send a test message:

```bash
curl -X POST http://localhost:8080/v1/messages?wait=true \
  -H "Authorization: Bearer tc_xxxxxxxx_..." \
  -H "Content-Type: application/json" \
  -d '{"to": "alerts", "text": "Hello from topicast", "level": "success"}'
```

Interactive API docs live at <http://localhost:8080/docs>.

> **Getting the ids**: create the bot with [@BotFather](https://t.me/BotFather), add it to your
> group as an admin, enable **Topics**, then follow
> [Finding your ids](https://develoverli.github.io/topicast/telegram-setup/).

## Configuration

`config.yaml` holds everything that is not a secret; `${VAR}` reads from the environment.

```yaml
bots:
  default:
    token: ${TELEGRAM_BOT_TOKEN}

chats:
  homelab:
    id: ${TELEGRAM_CHAT_ID}

aliases:
  alerts:   { chat: homelab, topic: 5, dedupe_window: 120 }
  deploys:  { chat: homelab, topic: 7 }
  backups:  { chat: homelab, topic: 9, silent_levels: [info, success, warning] }
```

Full reference: [Configuration](https://develoverli.github.io/topicast/configuration/).

## Webhooks

Point any of these at topicast and they land in the right topic:

```
POST /v1/hooks/github/deploys?token=<key>
POST /v1/hooks/uptime-kuma/alerts?token=<key>
POST /v1/hooks/alertmanager/alerts?token=<key>
POST /v1/hooks/slack/alerts?token=<key>        # tools that only speak Slack
POST /v1/hooks/discord/alerts?token=<key>      # …or only Discord
POST /v1/hooks/generic/alerts?token=<key>&template=plain
```

Keys used in webhook URLs are created with `--scope hooks`, so a leaked URL cannot be used
for anything else. See [Webhooks](https://develoverli.github.io/topicast/webhooks/).

## Security

topicast is meant to run on a private network (LAN, Tailscale, or behind a reverse proxy
with TLS). API keys are stored as HMAC-SHA256 hashes, never in clear. Read
[Security](https://develoverli.github.io/topicast/security/) before exposing it publicly,
and report vulnerabilities through [SECURITY.md](SECURITY.md).

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup (`uv sync`, `ruff`, `mypy`, `pytest`).

## License

[MIT](LICENSE) © develoverli
