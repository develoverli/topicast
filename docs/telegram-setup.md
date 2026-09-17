# Telegram setup

topicast needs three things: a bot token, a chat id, and one topic id per alias.

## 1. Create the bot

1. Open [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Pick a name and a username.
3. Copy the token (`123456789:AA...`) into `TELEGRAM_BOT_TOKEN`.

Recommended: send `/setprivacy` → your bot → **Enable**. topicast never reads messages, so the
bot does not need to see group chatter.

## 2. Create the group and enable topics

1. Create a group (or use an existing one) and add your bot.
2. Promote the bot to **admin**. Without admin rights it cannot post to topics.
3. Group settings → **Topics** → enable. Telegram converts the group to a forum.
4. Create one topic per concern: `alerts`, `deploys`, `backups`, …

## 3. Find the chat id

Send any message in the group, then:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"chat":{"id":[-0-9]*'
```

Supergroup ids are negative and start with `-100`, e.g. `-1001234567890`. That value goes into
`TELEGRAM_CHAT_ID`.

!!! tip "Nothing in `getUpdates`?"
    Telegram only returns recent updates, and only if no webhook is set. Post a new message in
    the group and try again, or add [@RawDataBot](https://t.me/RawDataBot) temporarily.

## 4. Find the topic ids

In Telegram Desktop, right-click the topic → **Copy Link**. The link looks like:

```
https://t.me/c/1234567890/7/1
                          ▲
                          topic id
```

That number is the `topic:` value in `config.yaml`. It is the `message_thread_id` Telegram
expects — the id of the message that opened the topic.

Alternatively, post in the topic and read `message_thread_id` from `getUpdates`.

## 5. Check it

```bash
topicast check-config
```

```
config.yaml: OK
  alerts   → chat -1001234567890 (homelab), topic 5, dedupe 60s, bot default
  deploys  → chat -1001234567890 (homelab), topic 7, dedupe 60s, bot default
```

Then send a test message and confirm it lands in the right topic:

```bash
topicast send alerts "hello from topicast"
```

## Common errors

| Telegram error | Cause |
|---|---|
| `Forbidden: bot is not a member of the supergroup chat` | The bot was never added, or was removed |
| `Bad Request: message thread not found` | Wrong `topic:` id, or the topic was deleted |
| `Bad Request: chat not found` | Wrong `TELEGRAM_CHAT_ID`, or the `-100` prefix is missing |
| `Bad Request: not enough rights to send text messages` | The bot is not an admin, or the topic is closed |
| `chat was upgraded to a supergroup` | The group became a supergroup; use the new id from the error |

## Limits worth knowing

- 4096 characters per message (topicast splits longer text), 1024 per media caption.
- Roughly 20 messages per minute per group, and 30 per second overall per bot.
- 50 MB per uploaded file; 10 items per media group.
