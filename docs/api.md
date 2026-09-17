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
  "delivered_at": "2026-09-17T10:00:01Z",
  "fallback_reason": null,
  "last_error": null,
  "deduplicated": false
}
```

`status` is one of `queued`, `sending`, `delivered`, `failed`, `deleted`.

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

Match on `code`, not on the prose in `detail`:

| Code | Status | Meaning |
|---|---|---|
| `missing_api_key` | 401 | No key was sent. |
| `invalid_api_key` | 401 | Unknown or revoked key. |
| `missing_scope` | 403 | The key lacks `send`, `edit` or `hooks`. |
| `alias_not_allowed` | 403 | The key may not use this alias. |
| `unknown_alias` | 404 | The alias is not in `config.yaml`. |
| `message_not_found` | 404 | No such message id. |
| `idempotency_conflict` | 409 | Key reused with a different request. |
| `not_delivered` | 409 | The message cannot be edited yet. |
| `sending` | 409 | The message is mid-delivery; retry shortly. |
| `validation_error` | 422 | Invalid fields (see `errors`). |
| `text_too_long` | 422 | Over the limit with `on_overflow=reject`. |
| `telegram_rejected` | 422 | Telegram refused the message. |
| `telegram_rate_limited` | 429 | Flood limit hit; see `Retry-After`. |
| `delivery_failed` | 502 | Delivery failed while waiting. |

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
