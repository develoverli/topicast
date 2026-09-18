# Configuration

topicast reads two things:

| Source | Holds | Reload |
|---|---|---|
| Environment (`TOPICAST_*`) | secrets, ports, limits | restart |
| `config.yaml` | bots, chats, aliases, hook templates | restart |

`config.yaml` is safe to commit: secrets are referenced with `${VAR}`, never written inline.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TOPICAST_SECRET_KEY` | *(required)* | Pepper used to hash API keys. At least 32 characters. Changing it invalidates every key. |
| `TOPICAST_CONFIG_FILE` | `config.yaml` | Path to the YAML file (`/config/config.yaml` in the image). |
| `TOPICAST_DATA_DIR` | `data` | SQLite database and upload spool (`/data` in the image). |
| `TOPICAST_HOST` | `0.0.0.0` | Bind address inside the container. Leave it; to control who can reach the service, use `TOPICAST_BIND_ADDRESS` in `compose.yaml`. |
| `TOPICAST_PORT` | `8080` | Port the service listens on. With the bundled `compose.yaml` it is also the published host port, so changing it here is enough. |
| `TOPICAST_LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error`. |
| `TOPICAST_LOG_FORMAT` | `json` | `json` for production, `console` for local work. |
| `TOPICAST_DOCS_ENABLED` | `true` | Serve `/docs` and `/openapi.json`. |
| `TOPICAST_METRICS_ENABLED` | `true` | Serve `/metrics`. |
| `TOPICAST_WAIT_TIMEOUT` | `30` | Seconds `?wait=true` waits before answering `202`. |
| `TOPICAST_MAX_UPLOAD_MB` | `50` | Per-file upload limit (Telegram's own limit is 50 MB). |
| `TOPICAST_RETENTION_DAYS` | `7` | How long delivered and failed messages stay in the database. |
| `TOPICAST_IDEMPOTENCY_HOURS` | `24` | How long an `Idempotency-Key` is remembered. |
| `TOPICAST_MAX_ATTEMPTS` | `5` | Delivery attempts before a message is marked `failed`. |
| `TOPICAST_POLL_INTERVAL` | `1.0` | Queue poll interval in seconds. |
| `TOPICAST_TRUSTED_PROXIES` | `127.0.0.1` | Proxies allowed to set `X-Forwarded-For`. |
| `TOPICAST_CORS_ORIGINS` | *(empty)* | Browser origins allowed to call the API, comma separated. Empty disables CORS. See [Calling from a browser](#calling-from-a-browser). |

## config.yaml

```yaml
bots:
  default:
    token: ${TELEGRAM_BOT_TOKEN}
    rate_per_second: 30

chats:
  homelab:
    id: ${TELEGRAM_CHAT_ID}
    bot: default
    rate_per_minute: 20
  clients:
    id: ${TELEGRAM_CHAT_ID_CLIENTS}
    bot: default
  status:
    id: "@mystatuschannel"
    bot: default

aliases:
  alerts:
    chat: homelab
    topic: 5
    dedupe_window: 120
    silent_levels: [info]
    description: Infrastructure alerts
  support:
    chat: clients
    description: Customer group without topics
  incidents:
    chat: status

defaults:
  dedupe_window: 60
  silent_levels: [info, success]
  level_prefix:
    warning: "⚠️"

hooks:
  github:
    secret: ${GITHUB_WEBHOOK_SECRET:-}
    events: [push, release, workflow_run]
  templates:
    plain: "{{ payload.message }}"
```

### Variable substitution

- `${VAR}` — required. Startup fails with a clear error if it is unset.
- `${VAR:-fallback}` — optional, with a default.

### `bots`

| Key | Default | Description |
|---|---|---|
| `token` | *(required)* | Bot token from @BotFather. |
| `rate_per_second` | `30` | Telegram's global send limit for one bot. |

Several bots are allowed; a chat picks one with `bot:`. Each bot gets its own rate limiter,
which is the reason to add a second one: more headroom for a busy chat. The other reasons are
presentation — a different name and avatar in front of customers — and blast radius: a token
that leaks only reaches the chats that bot is in. One bot in every group is otherwise fine.

### `chats`

| Key | Default | Description |
|---|---|---|
| `id` | *(required)* | Numeric chat id (negative for groups) or `@channelusername`. |
| `bot` | `default` | Which bot posts here. |
| `rate_per_minute` | `20` for groups, `60` otherwise | Per-chat send rate. |

#### Several groups

There is no limit of one group. List as many chats as you want — groups with topics, plain
groups, and channels can coexist under the same bot:

```yaml
chats:
  homelab: { id: ${TELEGRAM_CHAT_ID} }          # forum group, aliases use `topic:`
  clients: { id: ${TELEGRAM_CHAT_ID_CLIENTS} }  # plain group, aliases omit `topic:`
  status:  { id: "@mystatuschannel" }           # channel, by username
```

Per extra group:

1. Add the variable to `.env` (`TELEGRAM_CHAT_ID_CLIENTS=-1009876543210`). A channel
   referenced by `@username` needs no variable.
2. Add the entry under `chats:` and at least one alias pointing at it.
3. Add the bot to that group **as an admin** — in a channel it needs post rights.
4. Restart: `docker compose restart topicast`. Configuration is read at startup.

Keys stay scoped per alias, so each service only reaches the groups you grant it:

```bash
topicast keys create homelab-svc --scope send --alias alerts --alias deploys
topicast keys create client-bot  --scope send --alias support
```

### `aliases`

The names your services use in `"to"`.

| Key | Default | Description |
|---|---|---|
| `chat` | *(required)* | A key from `chats`. |
| `topic` | *(none)* | Topic id. Omit to post in the group's General topic. |
| `dedupe_window` | `defaults.dedupe_window` | Seconds. `0` disables deduplication for this alias. |
| `silent_levels` | `defaults.silent_levels` | Levels delivered without a notification sound. |
| `quiet_hours` | `defaults.quiet_hours` | Local window with no sound, e.g. `"23:00-08:00"`. |
| `description` | — | Documentation only; shown by `topicast check-config`. |

### `defaults`

| Key | Default | Description |
|---|---|---|
| `dedupe_window` | `60` | Seconds an identical message is suppressed. |
| `silent_levels` | `[info, success]` | Levels that arrive silently. |
| `level_prefix` | `ℹ️ ✅ ⚠️ ❌ 🚨` | Emoji prefix per level. Set a level to `""` to disable it. |
| `timezone` | `UTC` | IANA name used to read `quiet_hours`, e.g. `Europe/Madrid`. |
| `quiet_hours` | *(none)* | Window applied to aliases without their own. |
| `quiet_exempt_levels` | `[critical]` | Levels that keep their sound during quiet hours. |

### `hooks`

| Key | Default | Description |
|---|---|---|
| `github.secret` | *(none)* | When set, `X-Hub-Signature-256` is required and verified. |
| `github.events` | all supported | Restrict which GitHub events produce a message. |
| `templates` | `{}` | Named Jinja2 templates for the generic webhook. |

## Calling from a browser

By default no browser can call topicast: without CORS headers the request is blocked. Server to
server callers are unaffected — CORS is a browser rule.

If you are building a small internal panel, list its origins:

```bash
TOPICAST_CORS_ORIGINS=https://panel.example.com,http://localhost:5173
```

```
GET  /v1/messages/{id}   allowed
POST /v1/messages        allowed
PATCH, DELETE            not exposed to browsers
```

Allowed request headers are `Authorization`, `X-API-Key`, `Content-Type` and
`Idempotency-Key`; `X-Request-ID` and `Retry-After` are readable from the response. Cookies are
never accepted, so `Access-Control-Allow-Credentials` is not sent.

!!! danger "A key in a browser is a public key"
    Anything the page can send, a visitor can read in DevTools and replay. Use a key restricted
    to `--scope send` and to the single alias that panel needs, and rotate it when the page
    changes hands. If the browser is only the messenger for your own backend, keep the key in
    the backend and leave CORS off.

`*` is rejected on purpose: with it, any website could use a visitor's key. Origins must be
`scheme://host[:port]`; a wrong value stops startup with an explicit error.

## Deduplication

Two requests collapse into one message when they land on the same alias within the window and
share the same identity:

- **Default identity**: a hash of the alias, kind, text, level and media.
- **Explicit identity**: the `dedupe_key` field — use it when the text changes but the event
  does not (`"dedupe_key": "disk-usage"`).

Suppressed messages are counted. When the window closes, topicast posts a summary:

```
🔁 Repeated 42× in the last 60s:
Disk at 91%
```

Set `dedupe_window: 0` on aliases where every message matters (deploy logs, audit trails).

## Levels

| Level | Prefix | Default sound |
|---|---|---|
| `info` | ℹ️ | silent |
| `success` | ✅ | silent |
| `warning` | ⚠️ | loud |
| `error` | ❌ | loud |
| `critical` | 🚨 | loud |

`"silent": true` or `false` in a request overrides the default for that message.

## Quiet hours

Nobody wants the 3 AM backup to buzz. A window silences an alias during those hours:

```yaml
defaults:
  timezone: Europe/Madrid        # quiet_hours is read in this zone
  quiet_hours: "23:00-08:00"     # applies to every alias…
  quiet_exempt_levels: [critical]

aliases:
  backups:
    chat: homelab
    topic: 9
    quiet_hours: "22:00-09:00"   # …unless the alias sets its own
  pager:
    chat: homelab
    topic: 11
    quiet_hours: "00:00-00:00"   # an empty window means never quiet
```

Windows may cross midnight. The start is inclusive and the end exclusive, so `23:00-08:00`
covers 23:00 through 07:59.

Who wins, in order:

1. `"silent": true` or `false` in the request — always.
2. Quiet hours, unless the level is in `quiet_exempt_levels` (`critical` by default).
3. `silent_levels` (`info` and `success` by default).

Messages still arrive on time; only the notification sound changes. To delay delivery instead,
see `send_at` in the [API reference](api.md).

## Rate limiting

Two token buckets guard every send: one per bot (`rate_per_second`) and one per chat
(`rate_per_minute`). The bot bucket is shared by every chat that bot posts in, so 30/s is the
ceiling across all your groups together, while each group keeps its own 20/minute. When Telegram answers `429`, the chat is paused for exactly as long as it
asks and the message is retried without burning an attempt.

## Reloading

Configuration is read at startup. After editing `config.yaml`:

```bash
docker compose restart topicast
```

Queued messages survive the restart — they live in SQLite, not in memory.
