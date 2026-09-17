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
| `topicast migrate` | Applies database migrations (the server does this automatically). |
| `topicast serve` | Runs the HTTP server. |
| `topicast version` | Prints the version. |

```console
$ topicast check-config
config.yaml: OK
  alerts   → chat -1001234567890 (homelab), topic 5, dedupe 60s, bot default
  deploys  → chat -1001234567890 (homelab), topic 7, dedupe 0s, bot default
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
