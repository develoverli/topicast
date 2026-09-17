# API reference

Base URL: `http://<host>:8080`. Interactive docs: `/docs`. Machine-readable: `/openapi.json`.

## Authentication

```
Authorization: Bearer tc_1a2b3c4d_...
```

`X-API-Key: <key>` works too. Webhook endpoints also accept `?token=<key>` because most
senders cannot set headers.

Every key carries **scopes** (`send`, `edit`, `hooks`) and a list of **aliases** it may use.

## POST /v1/messages

Queues a message. Returns `202` with an id; add `?wait=true` to wait for delivery.

### JSON body

| Field | Type | Default | Description |
|---|---|---|---|
| `to` | string | *(required)* | Alias from `config.yaml`. |
| `text` | string | — | Message text. Required unless a file is attached. |
| `parse_mode` | `HTML` · `MarkdownV2` · `null` | `null` | Markup. Invalid markup falls back to plain text. |
| `level` | `info` · `success` · `warning` · `error` · `critical` | — | Adds an emoji prefix and picks the sound. |
| `silent` | boolean | from `level` | Override the notification sound. |
| `disable_preview` | boolean | `false` | Hide link previews. |
| `dedupe_key` | string | — | Custom dedupe identity. |
| `on_overflow` | `split` · `truncate` · `reject` | `split` | What to do with text over 4096 characters. |
| `media` | array | `[]` | `[{ "type": "photo"\|"document", "url": "...", "filename": "..." }]`, up to 10. |
| `send_at` | string | — | ISO 8601 with a timezone. Delivers then instead of now, up to 365 days ahead. |

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"to": "alerts", "text": "<b>Disk</b> at 91%", "parse_mode": "HTML", "level": "warning"}'
```

### Uploading files

Send `multipart/form-data` with one or more `file` parts. `text` becomes the caption.

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Authorization: Bearer $KEY" \
  -F "to=alerts" -F "text=Nightly backup log" -F "file=@backup.log"
```

Extra form fields: `as` (`auto`, `photo`, `document`), `parse_mode`, `level`, `silent`,
`dedupe_key`, `on_overflow`. Images under 10 MB are sent as photos when `as=auto`; everything
else is sent as a document. Two or more files become an album (all of the same type).

### Query parameters

| Parameter | Default | Description |
|---|---|---|
| `wait` | `false` | Wait for delivery (up to `TOPICAST_WAIT_TIMEOUT`) and return the final state. |

### Headers

| Header | Description |
|---|---|
| `Idempotency-Key` | Retrying with the same key returns the original message instead of sending twice. Remembered for 24 hours. |
| `X-Request-ID` | Echoed back and included in logs and problem documents. |

### Responses

| Status | Meaning |
|---|---|
| `202` | Queued. `status` is `queued`. |
| `200` | Delivered (`wait=true`), deduplicated, or replayed from an `Idempotency-Key`. |
| `401` | Missing or invalid key. |
| `403` | The key lacks the scope or the alias. |
| `404` | Unknown alias. |
| `409` | `Idempotency-Key` reused with a different body. |
| `413` | Uploaded file over the limit. |
| `422` | Invalid body, or text too long with `on_overflow=reject`. |
| `502` | Delivery failed (only with `wait=true`). |

```json
{
  "id": "9f0b2c…",
  "status": "delivered",
  "to": "alerts",
  "kind": "text",
  "attempts": 0,
  "telegram_message_ids": [4711],
  "created_at": "2026-09-17T10:00:00Z",
  "scheduled_for": null,
  "delivered_at": "2026-09-17T10:00:01Z",
  "fallback_reason": null,
  "last_error": null,
  "deduplicated": false
}
```

`status` is one of `queued`, `sending`, `delivered`, `failed`, `deleted`.

### Scheduling

```bash
curl -X POST http://localhost:8080/v1/messages   -H "Authorization: Bearer $KEY" -H "Content-Type: application/json"   -d '{"to": "alerts", "text": "Certificate expires today", "send_at": "2026-12-01T08:00:00Z"}'
```

The answer is `202` with `scheduled_for` set. The message waits in the queue until then and
survives restarts, and `wait=true` returns immediately instead of burning the timeout. Cancel
it with `DELETE /v1/messages/{id}` any time before it goes out.

Times need a timezone (`Z` or an offset), cannot be in the past, and cannot be more than 365
days ahead. Delivery is best-effort to the second: the worker polls every
`TOPICAST_POLL_INTERVAL` and the chat rate limit still applies.

## GET /v1/messages/{id}

Current state of a message. Requires the `send` scope and access to its alias.

## PATCH /v1/messages/{id}

Edits a delivered message (`edit` scope).

```json
{ "text": "Disk back to 42%", "parse_mode": null, "level": "success", "on_overflow": "reject" }
```

The edited text must fit in one message (4096 characters, or 1024 for a media caption); use
`on_overflow: "truncate"` to cut it. If the original was split into several messages, only the
first one is edited.

## DELETE /v1/messages/{id}

Deletes the Telegram messages, or cancels the message if it has not been sent yet (`edit`
scope). Telegram only allows bots to delete messages younger than 48 hours.

## POST /v1/hooks/{source}/{alias}

See [Webhooks](webhooks.md).

## Health and metrics

| Endpoint | Description |
|---|---|
| `GET /healthz` | Liveness. Always `200` while the process runs. |
| `GET /readyz` | `200` when the database, the worker and every bot are healthy; `503` otherwise. |
| `GET /metrics` | Prometheus metrics. See [Operations](operations.md). |

## Errors

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem document with
`Content-Type: application/problem+json`:

```json
{
  "type": "about:blank",
  "title": "Forbidden",
  "status": 403,
  "code": "alias_not_allowed",
  "detail": "This key cannot send to 'alerts'.",
  "instance": "/v1/messages",
  "request_id": "3f9a1c2b7d4e5a60"
}
```

Match on `code`, not on the prose in `detail`.

**Authentication and permissions**

| Code | Status | Meaning |
|---|---|---|
| `missing_api_key` | 401 | No key was sent. |
| `invalid_api_key` | 401 | Unknown or revoked key. |
| `invalid_signature` | 401 | Webhook signature does not match (GitHub). |
| `missing_scope` | 403 | The key lacks `send`, `edit` or `hooks`. |
| `alias_not_allowed` | 403 | The key may not use this alias. |

**Your request**

| Code | Status | Meaning |
|---|---|---|
| `unknown_alias` | 404 | The alias is not in `config.yaml`. |
| `message_not_found` | 404 | No such message id, or it belongs to another key's aliases. |
| `unknown_source` | 404 | Unknown webhook source in the URL. |
| `unknown_template` | 404 | The `template` query parameter names a template that is not configured. |
| `validation_error` | 422 | Invalid fields. The `errors` array says which. |
| `invalid_json` | 422 | The body is not valid JSON. |
| `invalid_field` | 422 | A multipart field has an unusable value. |
| `missing_field` | 422 | A required multipart field (`to`) is absent. |
| `invalid_parse_mode` | 422 | `parse_mode` is not `html`, `markdownv2` or `plain`. |
| `empty_message` | 422 | Neither `text` nor a file was provided. |
| `text_too_long` | 422 | Over the limit with `on_overflow=reject`. |
| `mixed_media_group` | 422 | Photos and documents cannot travel in one album. |
| `too_many_files` | 422 | More than 10 files in one request. |
| `invalid_payload` | 422 | A webhook adapter could not read the payload. |
| `template_error` | 422 | The Jinja2 template failed to render. |
| `file_too_large` | 413 | An uploaded file exceeds `TOPICAST_MAX_UPLOAD_MB`. |
| `payload_too_large` | 413 | A webhook body exceeds 1 MB. |

**State conflicts**

| Code | Status | Meaning |
|---|---|---|
| `idempotency_conflict` | 409 | The `Idempotency-Key` was used with a different body. |
| `conflict` | 409 | Two concurrent requests used the same `Idempotency-Key`. |
| `not_delivered` | 409 | The message cannot be edited until it is delivered. |
| `sending` | 409 | The message is mid-delivery; retry shortly. |
| `not_failed` | 409 | Only failed messages can be retried. |
| `files_gone` | 409 | The uploaded files were cleaned up; send the message again. |

**Telegram and delivery**

| Code | Status | Meaning |
|---|---|---|
| `telegram_rejected` | 422 | Telegram refused the message (bad topic, bad markup on an edit). |
| `telegram_message_not_found` | 404 | The message no longer exists in Telegram. |
| `telegram_rate_limited` | 429 | Flood limit hit. Honour `Retry-After`. |
| `telegram_unavailable` | 502 | Telegram is unreachable right now. |
| `delivery_failed` | 502 | Delivery failed while waiting (`wait=true` only). |
| `internal_error` | 500 | Unexpected server error. Check the logs with the `request_id`. |

### What a caller should do

| Situation | Action |
|---|---|
| `401`, `403`, `404`, `409`, `413`, `422` | Do not retry. The request or the configuration is wrong. |
| `429` | Wait for `Retry-After`, then retry. Usually the queue absorbs this for you. |
| `500`, `502`, `504`, connection errors | Retry with backoff, sending the same `Idempotency-Key` so a delivered message is not duplicated. |
| `202` | Nothing to do. The message is queued; `GET /v1/messages/{id}` tells you how it ended. |

A `202` is the normal answer. Treating it as an error is the most common integration mistake:
the queue exists precisely so your service does not wait for Telegram.

## Where to point your service

topicast listens on plain HTTP and is not meant to be public, so the URL depends on where the
caller runs.

| The caller runs… | Base URL | What it needs |
|---|---|---|
| In the same `docker compose` file | `http://topicast:8080` | Nothing; Compose resolves the service name. |
| In another Compose project on the same host | `http://topicast:8080` | Join topicast's network (`networks: [topicast_default]`, `external: true`). |
| Directly on the host | `http://127.0.0.1:8080` | The port published to localhost. |
| On another machine over a VPN | `http://<vpn-host>:8080` | The port bound to the VPN address. With Docker, add `extra_hosts: ["<vpn-host>:100.x.y.z"]` so the name resolves without waiting for the VPN's DNS. |
| Outside your network | `https://topicast.example.com` | A reverse proxy with TLS. See [Deployment](deployment.md). |

Give the caller two environment variables and nothing else:

```bash
TOPICAST_URL=http://topicast:8080
TOPICAST_KEY=tc_1a2b3c4d_...
```

Store the key like any other secret (environment variable, Docker secret, your secret manager);
it is equivalent to permission to post in the aliases it was granted.

Check the wiring before writing code:

```bash
docker compose exec my-app sh -c 'wget -qO- $TOPICAST_URL/healthz'
# {"status":"ok","version":"0.5.0"}
```

| Failure | Cause |
|---|---|
| `Temporary failure in name resolution` | The hostname does not resolve from inside the container. Wrong network, or missing `extra_hosts`. |
| `No route to host` | The name resolved but the host is unreachable. VPN down on the caller's host. |
| `Connection refused` | Reached the host, nothing is listening. topicast is down, or bound to another address or port. |
| `401` | The URL is right; the key is wrong or absent. |

## Client examples

=== "Python"

    ```python
    import httpx


    def notify(text: str, *, to: str = "alerts", level: str | None = None) -> dict:
        response = httpx.post(
            "http://topicast:8080/v1/messages",
            json={"to": to, "text": text, "level": level},
            headers={"Authorization": f"Bearer {KEY}"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    ```

=== "Node.js"

    ```javascript
    async function notify(text, { to = 'alerts', level } = {}) {
      const response = await fetch('http://topicast:8080/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${process.env.TOPICAST_KEY}`,
        },
        body: JSON.stringify({ to, text, level }),
      });
      if (!response.ok) throw new Error(`topicast ${response.status}`);
      return response.json();
    }
    ```

=== "Shell"

    ```bash
    notify() {
      curl -fsS -X POST "$TOPICAST_URL/v1/messages" \
        -H "Authorization: Bearer $TOPICAST_KEY" \
        -H "Content-Type: application/json" \
        -d "$(jq -n --arg t "$1" '{to: "alerts", text: $t, level: "info"}')"
    }
    ```
