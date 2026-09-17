# Webhooks

Point a third-party service at topicast and its events land in a topic, formatted.

```
POST /v1/hooks/{source}/{alias}?token=<key>
```

| `{source}` | Sender |
|---|---|
| `github` | GitHub webhooks |
| `uptime-kuma` | Uptime Kuma "Webhook" notifications |
| `alertmanager` | Prometheus Alertmanager |
| `grafana` | Grafana unified alerting (same payload as Alertmanager) |
| `slack` | Anything that speaks "Slack incoming webhook" |
| `discord` | Anything that speaks "Discord webhook URL" |
| `generic` | Anything that posts JSON |

## Create a webhook key first

Webhook URLs end up in other systems' configuration, so give them a key that can do nothing
else — only `hooks`, only one alias:

```bash
topicast keys create uptime-kuma --scope hooks --alias alerts
```

An event the adapter does not handle returns `204`, not an error.

## GitHub

1. Repository → **Settings** → **Webhooks** → **Add webhook**.
2. Payload URL: `https://topicast.example.com/v1/hooks/github/deploys?token=tc_...`
3. Content type: `application/json`.
4. Secret: the same value as `GITHUB_WEBHOOK_SECRET` in your `.env`.
5. Events: *Let me select individual events* → Pushes, Pull requests, Releases, Workflow runs.

```yaml
hooks:
  github:
    secret: ${GITHUB_WEBHOOK_SECRET}
    events: [push, pull_request, release, workflow_run]
```

!!! warning "Set the secret"
    Without `hooks.github.secret`, signatures are not verified and anyone who learns the URL
    can post arbitrary GitHub-shaped events.

| Event | Sent when | Level |
|---|---|---|
| `ping` | The webhook is created | info |
| `push` | Commits are pushed (branch deletions ignored) | info |
| `pull_request` | Opened, reopened, ready for review, closed, merged | info / success |
| `release` | A release is published | success |
| `workflow_run` | A run completes | success / warning / error |

## Uptime Kuma

Settings → **Notifications** → **Setup Notification** → type **Webhook**:

- Post URL: `https://topicast.example.com/v1/hooks/uptime-kuma/alerts?token=tc_...`
- Request body: **application/json**

Down becomes `error`, up becomes `success`, maintenance becomes `info`. Repeats of the same
monitor in the same state are deduplicated automatically, so a flapping monitor produces one
message plus a `🔁 Repeated N×` summary instead of a wall of alerts.

## Alertmanager and Grafana

```yaml
receivers:
  - name: topicast
    webhook_configs:
      - url: https://topicast.example.com/v1/hooks/alertmanager/alerts?token=tc_...
        send_resolved: true
```

In Grafana: **Alerting** → **Contact points** → **Webhook**, same URL with
`/v1/hooks/grafana/alerts`.

Severity maps to levels (`critical` → critical, `warning` → warning, resolved → success), and
each alert line shows its `summary` annotation plus the `instance` label.

## Slack and Discord compatible

Plenty of self-hosted tools only offer a "Slack webhook URL" or a "Discord webhook URL" field.
Paste a topicast URL there and the notification lands in your Telegram topic — no code, no
plugin, no Slack or Discord account:

```
https://topicast.example.com/v1/hooks/slack/alerts?token=tc_...
https://topicast.example.com/v1/hooks/discord/alerts?token=tc_...
```

Works with Watchtower, Portainer, Sonarr and Radarr, Netdata, Gitea, Healthchecks.io, Zabbix,
and anything else that emits those payloads.

**What is translated**

| Slack | Discord | Becomes |
|---|---|---|
| `text` | `content` | The message body |
| `blocks` (section, header, context) | `embeds[].description` | Body lines |
| `attachments[].title` + `title_link` | `embeds[].title` + `url` | A bold line, linked when a URL is present |
| `attachments[].fields` | `embeds[].fields` | `<b>Name:</b> value` lines |
| `attachments[].footer` | `embeds[].footer.text` | An italic closing line |
| `attachments[].color` | `embeds[].color` | The message [level](configuration.md#levels) |
| — | `username` | A bold heading when there is no `content` |

Colors decide the level, so the emoji prefix and the notification sound follow what the tool
already meant: `good` or green means success, `warning` or yellow means warning, `danger` or
red means error.

Slack mrkdwn (`*bold*`, `_italic_`, `` `code` ``, `<url|label>`) and Discord markdown
(`**bold**`, `*italic*`, `~~strike~~`, `[label](url)`) are converted to Telegram HTML.
Everything else is escaped, so a payload cannot inject markup.

Try it without leaving the terminal:

```bash
curl -X POST "http://localhost:8080/v1/hooks/slack/alerts?token=tc_..." \
  -H "Content-Type: application/json" \
  -d '{"attachments":[{"color":"danger","title":"Disk full","text":"/var at 98%"}]}'
```

Not supported, because Telegram has no equivalent: interactive components, thumbnails and
images inside embeds, mentions, and ephemeral messages. The text is kept; the rest is dropped.

## Generic JSON

Without a template, topicast looks for `title` and `text`/`message`/`body`; anything else is
posted as pretty-printed JSON in a `<pre>` block. A `level` field in the payload is honoured.

```bash
curl -X POST "https://topicast.example.com/v1/hooks/generic/alerts?token=tc_..." \
  -H "Content-Type: application/json" \
  -d '{"title": "Backup", "message": "finished in 4m", "level": "success"}'
```

### Templates

Define named [Jinja2](https://jinja.palletsprojects.com/) templates in `config.yaml` and pick
one with `?template=`:

```yaml
hooks:
  templates:
    backup: |
      <b>Backup {{ payload.job }}</b>
      status: {{ payload.status | html }}
      size: {{ payload.size | html }}
```

```
POST /v1/hooks/generic/backups?token=tc_...&template=backup
```

| Query parameter | Default | Description |
|---|---|---|
| `template` | — | Template name from `hooks.templates`. |
| `parse_mode` | `html` | `html`, `markdownv2` or `plain`. |
| `level` | — | Level for every message from this URL. |

The payload is available as `payload`. Use the `| html` filter on values you interpolate into
HTML — templates are rendered in a sandbox, but escaping is still yours to do.

Templates are code: only add ones you wrote or reviewed.

## Writing a new adapter

Adapters are ~40 lines: parse a payload, return a `HookMessage`. See
[Contributing](https://github.com/develoverli/topicast/blob/main/CONTRIBUTING.md#writing-a-webhook-adapter).
