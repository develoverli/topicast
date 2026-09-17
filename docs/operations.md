# Operations

## Health

| Endpoint | Use it for |
|---|---|
| `/healthz` | Liveness. `200` while the process is alive. |
| `/readyz` | Readiness. `200` only when the database, the worker and every bot are healthy. |

```json
{
  "status": "ready",
  "version": "0.1.0",
  "database": true,
  "worker": true,
  "bots": { "default": "ok" }
}
```

A bot whose token is wrong or that was removed from the group shows up here as
`"bots": {"default": "error: ..."}` and the endpoint answers `503`. Bot checks are cached for
60 seconds, so probing often is cheap.

## Metrics

`/metrics` exposes Prometheus metrics:

| Metric | Type | Labels |
|---|---|---|
| `topicast_messages_accepted_total` | counter | `alias`, `source` |
| `topicast_messages_delivered_total` | counter | `alias` |
| `topicast_messages_failed_total` | counter | `alias` |
| `topicast_messages_deduplicated_total` | counter | `alias` |
| `topicast_markup_fallbacks_total` | counter | `alias` |
| `topicast_telegram_retries_total` | counter | `reason` (`rate_limited`, `transient`) |
| `topicast_queue_depth` | gauge | — |
| `topicast_delivery_latency_seconds` | histogram | — |
| `topicast_http_requests_total` | counter | `method`, `route`, `status` |

Alerts worth having:

```yaml
- alert: TopicastQueueBacklog
  expr: topicast_queue_depth > 50
  for: 10m

- alert: TopicastDeliveryFailures
  expr: increase(topicast_messages_failed_total[15m]) > 0

- alert: TopicastMarkupFallbacks
  expr: increase(topicast_markup_fallbacks_total[1h]) > 5
```

A rising `topicast_markup_fallbacks_total` means a caller is sending broken Markdown or HTML:
the message still arrives, but as plain text.

## Logs

JSON, one object per line:

```json
{"event": "message_delivered", "message_id": "9f0b…", "alias": "alerts",
 "telegram_message_ids": [4711], "request_id": "3f9a…", "level": "info",
 "timestamp": "2026-09-17T10:00:01Z"}
```

Useful events: `message_accepted`, `message_delivered`, `message_failed`, `delivery_retry`,
`telegram_rate_limited`, `markup_fallback`, `auth_failed`, `hook_ignored`.

Query strings are never logged, because webhook URLs carry a token. Set
`TOPICAST_LOG_FORMAT=console` for readable local output.

## The queue

```bash
topicast messages list                 # last 20
topicast messages list --status failed
topicast messages retry <id>
```

| Status | Meaning |
|---|---|
| `queued` | Waiting for the worker or for a retry. |
| `sending` | In flight. |
| `delivered` | Accepted by Telegram. |
| `failed` | Gave up after `TOPICAST_MAX_ATTEMPTS`, or Telegram refused it permanently. |
| `deleted` | Deleted through the API. |

Retry policy: transient errors back off exponentially (~2s, 4s, 8s … capped at 5 minutes, with
jitter). Telegram `429` responses pause that chat for exactly the requested time and do not
count as an attempt. Permanent errors (chat not found, bot kicked, invalid topic) fail
immediately — retrying them would not help.

Delivery is **at-least-once**: if the process dies between the Bot API call and the database
write, the message can be sent twice on restart.

## Retention

Delivered and failed messages are deleted after `TOPICAST_RETENTION_DAYS` (7 by default).
Idempotency keys are forgotten after `TOPICAST_IDEMPOTENCY_HOURS` (24). Uploaded files are
removed as soon as the message is delivered; orphans are swept after 24 hours.

## Common problems

| Symptom | Cause and fix |
|---|---|
| `/readyz` is `503`, bots show an error | Wrong token, or the bot was removed from the group. |
| Messages stay `queued` | The worker is paused by a `429`, or the chat rate limit is very low. Check `telegram_rate_limited` in the logs. |
| `fallback_reason` is set | The caller's markup is invalid. Escape the text or drop `parse_mode`. |
| Everything gets deduplicated | The alias sends identical text; use `dedupe_key`, or set `dedupe_window: 0`. |
| `message thread not found` | The `topic:` id in `config.yaml` is wrong or the topic was deleted. |
| `database is locked` | Two instances share one volume. Run only one. |

## Updating a key

Keys cannot be rotated in place — create a new one, update the service, then revoke the old:

```bash
topicast keys create my-service-v2 --scope send --alias alerts
topicast keys revoke my-service
```
