# Deploying on Dokploy

[Dokploy](https://dokploy.com/) is a self-hosted PaaS: a web UI on top of Docker. This page is
the whole path, from an empty project to a message landing in your Telegram topic.

Ten minutes, no terminal beyond two copy-paste commands.

!!! info "Before you start"
    You need the bot token, the group id and your topic ids. If you do not have them yet, do
    [Telegram setup](telegram-setup.md) first — it takes about two minutes.

## 1. Create the service

In your Dokploy project: **Create Service → Compose**.

Give it a name (`topicast`) and paste this into **Compose File**:

```yaml
services:
  topicast:
    image: ghcr.io/develoverli/topicast:0.6.0
    restart: unless-stopped
    environment:
      TOPICAST_SECRET_KEY: ${TOPICAST_SECRET_KEY}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
      # One variable per extra group, mirrored in config.yaml:
      # TELEGRAM_CHAT_ID_CLIENTS: ${TELEGRAM_CHAT_ID_CLIENTS}
      TOPICAST_PORT: 8080
    ports:
      - "8080:8080"
    volumes:
      - topicast-data:/data
      - ../files/config.yaml:/config/config.yaml:ro

volumes:
  topicast-data:
```

Pin the version (`:0.6.0`) rather than `:latest`, so a redeploy never surprises you with a
different build. The [releases page](https://github.com/develoverli/topicast/releases) lists
what changed in each one.

!!! danger "The port must be the same on both sides"
    `TOPICAST_PORT` and both halves of `"8080:8080"` are one number. Mapping `8339:8080`
    produces a service nothing can reach, and the logs look perfectly healthy while it happens.
    To move it to another port, change all three occurrences together.

## 2. Add the secrets

Open the **Environment** tab and paste:

```bash
TOPICAST_SECRET_KEY=paste-32-random-characters-here
TELEGRAM_BOT_TOKEN=123456:your-token-from-botfather
TELEGRAM_CHAT_ID=-1001234567890
# TELEGRAM_CHAT_ID_CLIENTS=-1009876543210   # one line per extra group
```

Generate the secret key with `openssl rand -base64 32` on any machine, or take any long random
string. It is the pepper that hashes your API keys — **keep it**. Changing it later invalidates
every key you issued.

## 3. Add config.yaml as a file mount

This is the step people miss. topicast reads its aliases from a YAML file, and Dokploy has a
dedicated place for those.

Go to **Advanced → Volumes → Add Volume**, choose **File Mount**, and fill in:

| Field | Value |
|---|---|
| Mount path | `config.yaml` |
| Content | the YAML below |

```yaml
bots:
  default:
    token: ${TELEGRAM_BOT_TOKEN}

chats:
  homelab:
    id: ${TELEGRAM_CHAT_ID}
  # A second group, same bot. Declare the variable in the Environment tab too.
  # clients:
  #   id: ${TELEGRAM_CHAT_ID_CLIENTS}

aliases:
  alerts:
    chat: homelab
    topic: 5
  deploys:
    chat: homelab
    topic: 7
  # support:
  #   chat: clients
```

Add as many groups as you want here — the bot must be an admin in each. Redeploy the service
after editing this file; the configuration is read at startup. See
[Several groups](configuration.md#several-groups).

Replace `5` and `7` with your own topic ids, and add one alias per topic you want to post to.
The `${...}` values are read from the environment you set in step 2, so no secret is written
into this file.

Dokploy stores file mounts under `../files/`, which is exactly what the compose file mounts
into the container. Leave that line as it is.

## 4. Deploy

Press **Deploy** and open **Logs**. A healthy start looks like this:

```json
{"event": "bot_ready", "bot": "default", "username": "homelab_bot", "level": "info"}
{"event": "topicast_started", "bots": 1, "chats": 1, "aliases": ["alerts", "deploys"]}
```

If instead you see `config file not found`, the file mount name or path does not match step 3.

## 5. Check it against Telegram

Open the service's **Terminal** in Dokploy and run:

```bash
topicast doctor
```

```console
✔ storage: /data is writable
✔ database: /data/topicast.db is reachable
! api keys: no active keys
  → Create one: topicast keys create <service> --scope send --alias <alias>
✔ bot 'default': @homelab_bot (id 7627…)
✔ chat 'homelab': Homelab (supergroup)
✔ chat 'homelab' membership: bot is administrator
```

This asks Telegram directly, so it catches the real problems: a wrong token, a bot that was
never added to the group, a group without Topics enabled. Fix anything marked `✖` before
continuing — see [the check table](cli.md#doctor).

## 6. Create a key and send a test

Still in the terminal:

```bash
topicast keys create my-first-service --scope send --alias alerts
```

The key is printed once, `tc_1a2b3c4d_…`. Copy it now.

Then prove the whole path works, including the topic ids:

```bash
topicast doctor --probe
```

That sends a real message to every alias and deletes it again. If each alias says
`delivered and cleaned up`, your deployment is done.

## 7. Point your other services at it

Give each service two environment variables and nothing else:

```bash
TOPICAST_URL=http://<your-host>:8080
TOPICAST_KEY=tc_1a2b3c4d_...
```

| Where the caller runs | What to use |
|---|---|
| Another Dokploy service on the same server | `http://<compose-service-name>:8080`, after joining topicast's network |
| Directly on the host | `http://127.0.0.1:8080` |
| Another machine over a VPN | the VPN address, plus `extra_hosts` if it is a container |

Give every service its **own** key, restricted to the aliases it needs:

```bash
topicast keys create uptime-kuma --scope hooks --alias alerts
topicast keys create ci-pipeline --scope send  --alias deploys
```

If one leaks, you revoke that one and nothing else breaks. See
[Security](security.md#scopes-and-aliases).

## Optional: a domain with TLS

Only needed if something outside your network must reach topicast. Webhooks from GitHub or a
cloud service are the usual reason; your own servers are not.

In **Domains → Add Domain**:

| Field | Value |
|---|---|
| Host | `topicast.example.com` |
| Container port | `8080` |
| HTTPS | on, with Let's Encrypt |

Traefik takes care of the certificate. Then remove the `ports:` block from the compose file so
the service is reachable only through the proxy, and read
[Security](security.md#exposure) before leaving it public.

## Updating

1. Change the tag in the compose file (`:0.6.0` → `:0.7.0`).
2. **Deploy**.

Database migrations run by themselves at startup. Queued messages survive the restart, because
they live in the SQLite file on the volume, not in memory.

To roll back, put the old tag back and deploy again.

## Backups

The volume `topicast-data` holds `topicast.db`: your API key hashes and recent message history.
Back it up like any other database, and treat the copy as a secret:

```bash
docker compose exec topicast \
  python -c "import sqlite3; sqlite3.connect('/data/topicast.db').backup(sqlite3.connect('/data/backup.db'))"
```

## When something is wrong

| Symptom | Cause |
|---|---|
| Nothing responds on the port | `TOPICAST_PORT` and the published port differ. See step 1. |
| Logs say `config file not found` | The file mount is not landing at `/config/config.yaml`. |
| Logs say `environment variable 'X' is not set` | A `${...}` in `config.yaml` has no matching variable in **Environment**. |
| `doctor` says `chat not found` | Wrong `TELEGRAM_CHAT_ID`, or the bot was never added to the group. |
| `doctor` says the group is not a forum | Topics are not enabled in the group settings. |
| Messages return `202` but never arrive | Run `topicast doctor --probe`; usually a wrong topic id. |
| `database is locked` | Two instances share one volume. Run a single replica. |

!!! warning "Run one instance"
    The delivery worker lives inside the API process and SQLite takes one writer. Do not scale
    the service to several replicas.

More symptoms and the metrics to watch: [Operations](operations.md).
