# Quickstart

You need Docker and a Telegram group with topics enabled. If you do not have the bot and the
ids yet, do [Telegram setup](telegram-setup.md) first — it takes about two minutes.

## 1. Get the files

```bash
mkdir topicast && cd topicast
curl -O https://raw.githubusercontent.com/develoverli/topicast/main/compose.yaml
curl -o config.yaml https://raw.githubusercontent.com/develoverli/topicast/main/config.example.yaml
```

## 2. Set the secrets

```bash
cat > .env <<EOF
TOPICAST_SECRET_KEY=$(openssl rand -base64 32)
TELEGRAM_BOT_TOKEN=123456:your-token-from-botfather
TELEGRAM_CHAT_ID=-1001234567890
EOF
chmod 600 .env
```

!!! warning "`TOPICAST_SECRET_KEY` hashes your API keys"
    Keep it. If you change it, every issued key stops working.

Is 8080 taken on your machine, or do you want other hosts to reach it? Add these to the same
`.env` — the bundled `compose.yaml` uses them for both the container and the published port:

```bash
TOPICAST_PORT=9099                 # any free port
TOPICAST_BIND_ADDRESS=127.0.0.1    # 100.x.y.z for a VPN address, 0.0.0.0 for everyone
```

Every URL below then uses your port instead of 8080.

## 3. Point the aliases at your topics

Edit `config.yaml` so each alias matches a topic in your group:

```yaml
aliases:
  alerts:  { chat: homelab, topic: 5 }
  deploys: { chat: homelab, topic: 7 }
```

## 4. Start it

```bash
docker compose up -d
docker compose logs -f topicast     # should print "bot_ready" and "topicast_started"
curl -s http://127.0.0.1:8080/readyz
```

## 5. Create a key and send a message

```bash
docker compose exec topicast topicast keys create my-service --scope send --alias alerts
```

The key is printed once. Use it:

```bash
curl -X POST "http://127.0.0.1:8080/v1/messages?wait=true" \
  -H "Authorization: Bearer tc_xxxxxxxx_..." \
  -H "Content-Type: application/json" \
  -d '{"to": "alerts", "text": "topicast is live", "level": "success"}'
```

```json
{
  "id": "6f1c…",
  "status": "delivered",
  "to": "alerts",
  "telegram_message_ids": [1234],
  "deduplicated": false
}
```

Interactive docs: <http://127.0.0.1:8080/docs>.

## Without Docker

```bash
uv tool install git+https://github.com/develoverli/topicast
export TOPICAST_SECRET_KEY=...
topicast init              # writes config.yaml
topicast check-config
topicast serve
```

## Next steps

- [Send files, edit messages and use levels](api.md)
- [Forward GitHub, Uptime Kuma and Alertmanager events](webhooks.md)
- [Expose it safely](security.md)
