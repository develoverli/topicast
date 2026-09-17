# CLI

The image ships the `topicast` command; run it inside the container:

```bash
docker compose exec topicast topicast <command>
```

Outside Docker it is on your `PATH` after
`uv tool install git+https://github.com/develoverli/topicast`.

Every command reads the same environment as the server (`TOPICAST_SECRET_KEY`,
`TOPICAST_DATA_DIR`, `TOPICAST_CONFIG_FILE`).

## Setup

| Command | What it does |
|---|---|
| `topicast init [--config PATH] [--force]` | Writes a starter `config.yaml` and prints a generated `TOPICAST_SECRET_KEY`. |
| `topicast check-config` | Validates the config and prints what each alias resolves to. |
| `topicast doctor [--probe]` | Checks the deployment against Telegram. |
| `topicast migrate` | Applies database migrations (the server does this automatically). |
| `topicast serve` | Runs the HTTP server. |
| `topicast version` | Prints the version. |

```console
$ topicast check-config
config.yaml: OK
  alerts   → chat -1001234567890 (homelab), topic 5, dedupe 60s, bot default
  deploys  → chat -1001234567890 (homelab), topic 7, dedupe 0s, bot default
```

## doctor

`check-config` only proves the YAML parses. `doctor` asks Telegram and the filesystem whether
this deployment actually works:

```console
$ topicast doctor
✔ storage: /data is writable
✔ database: /data/topicast.db is reachable
✔ api keys: 3 active
✔ bot 'default': @homelab_bot (id 7627…)
✔ chat 'homelab': Homelab (supergroup)
✔ chat 'homelab' membership: bot is administrator
! chat 'staging' membership: the bot is 'member', not an admin
  → Posting to topics usually requires admin rights.

6 ok, 1 warnings, 0 failures
Run `topicast doctor --probe` to send a real test message to every alias.
```

| Check | Fails when |
|---|---|
| storage | The data directory is not writable — usually a volume mounted read-only or owned by another uid. |
| database | SQLite cannot be opened, or two instances share the volume. |
| api keys | *(warning)* No key exists yet, so nothing can send. |
| bot | The token is wrong or revoked. |
| chat | The chat id is wrong, or the bot was never added. |
| topics | Aliases point at topics but the group is not a forum. |
| membership | The bot was removed. *(warning)* It is in the group but not an admin. |

`--probe` goes further: it sends a real message to **every alias** and deletes it again. That
is the only way to know a topic id is right, because Telegram offers no way to read topics.
The probes are silent and short-lived, though group members may briefly see them.

`doctor` exits with status `1` when any check fails, so it works in a deploy pipeline:

```bash
docker compose exec -T topicast topicast doctor --probe || echo "topicast is misconfigured"
```

## Keys

```bash
topicast keys create <name> [--scope send|edit|hooks]... [--alias NAME|*]...
topicast keys list [--show-revoked]
topicast keys revoke <name>
```

Defaults: `--scope send --alias '*'`. The key is printed once:

```console
$ topicast keys create uptime-kuma --scope hooks --alias alerts
key 'uptime-kuma' created (id 1a2b3c4d)
scopes: hooks   aliases: alerts

tc_1a2b3c4d_9f3c…

Copy it now — it cannot be shown again.
```

## Queue

```bash
topicast messages list [--status queued|sending|delivered|failed] [--limit N]
topicast messages retry <id>
```

```console
$ topicast messages list --status failed
ID            STATUS      ALIAS           CREATED (UTC)         ERROR
7f3a91c4bd21  failed      alerts          2026-09-17 09:14:02   Bad Request: message thread not found
```

## Sending

```bash
topicast send <alias> "<text>" [--level warning]
```

Queues the message in the same database the server reads, so the running server delivers it.
Handy for smoke tests after a config change:

```bash
topicast send alerts "config reloaded" --level info
```
