# FAQ

**How is this different from Apprise, ntfy or Gotify?**
Those are general notification routers (many services, or their own app and clients). topicast
does one thing: Telegram forum topics, with the operational parts that matter when many
services share one group — per-service keys, per-chat rate limiting, deduplication,
idempotency, webhook adapters and a queue that survives restarts.

**Do I need a Telegram group with topics?**
No. Omit `topic:` in an alias and messages go to the group's General topic, or to a plain group
or channel. Topics are what makes one group usable as many inboxes, though.

**Can I use several groups or several bots?**
Yes. Add more entries under `chats` (and `bots`, if you want separate rate limits or
identities) and point aliases at them.

**Is the queue durable?**
Yes — it is a SQLite table on the volume. Messages queued before a restart are delivered after
it. Delivery is at-least-once: a crash between the Telegram call and the database write can
repeat a message.

**Can I run more than one replica?**
No. The worker lives in the API process and SQLite has one writer. Run a single instance; it
handles far more than a Telegram group's 20 messages per minute anyway.

**Why is my message silent?**
`info` and `success` are delivered without a sound by default. Override per message with
`"silent": false`, or per alias with `silent_levels`.

**Why did my message arrive as plain text with asterisks?**
Telegram rejected the markup, so topicast re-sent it as plain text rather than dropping it. The
response carries `fallback_reason`. Escape the reserved characters — MarkdownV2 needs
`_ * [ ] ( ) ~ \` > # + - = | { } . !` escaped with a backslash.

**Where did my repeated alerts go?**
Deduplication. Identical messages to the same alias within the window are counted and
summarised as `🔁 Repeated N×`. Use `dedupe_key` for a stable identity, or
`dedupe_window: 0` to switch it off for an alias.

**Can I edit or delete a message later?**
Yes, with a key that has the `edit` scope: `PATCH /v1/messages/{id}` and `DELETE
/v1/messages/{id}`. Telegram only lets bots delete messages younger than 48 hours.

**How large can attachments be?**
50 MB per file — that is the Bot API limit for bots. Images under 10 MB are sent as photos;
anything else as a document.

**Does it read messages or replies from Telegram?**
No. topicast only sends. It never polls `getUpdates` and never registers a Telegram webhook, so
it cannot see what people write in the group.

**Can I run it without Docker?**
Yes: `uv tool install git+https://github.com/develoverli/topicast`, set the environment
variables, then `topicast serve`. See
[Deployment](deployment.md).

**How do I back it up?**
Copy `/data/topicast.db` with SQLite's backup API (see [Deployment](deployment.md)). It
contains key hashes and recent message history — treat it as a secret.

**Something is wrong. Where do I look first?**
`GET /readyz`, then `docker compose logs topicast`, then `topicast messages list --status
failed`. [Operations](operations.md) lists the common failures.
