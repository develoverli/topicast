"""Discord-compatible webhook.

Tools that only speak "Discord webhook URL" (Watchtower, Sonarr, Portainer, …) can post to a
Telegram topic by pointing at this endpoint.
"""

from __future__ import annotations

from typing import Any

from topicast.config import AppConfig, Level
from topicast.hooks import HookError, HookMessage, HookRequest
from topicast.hooks.markup import discord_to_html

MAX_FIELDS = 10
MAX_EMBEDS = 5

# Discord colors are decimal RGB. Judge by hue so any shade of red means an error.
_GREEN, _YELLOW, _RED = Level.SUCCESS, Level.WARNING, Level.ERROR


def _level(color: object) -> Level | None:
    if not isinstance(color, int | str):
        return None
    try:
        value = int(color)
    except (TypeError, ValueError):
        return None
    if not 0 <= value <= 0xFFFFFF:
        return None
    red, green, blue = (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
    if red > 150 and green < 100 and blue < 100:
        return _RED
    if red > 180 and green > 140 and blue < 100:
        return _YELLOW
    if green > 120 and red < 150:
        return _GREEN
    return None


def _embed(embed: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    author = embed.get("author")
    if isinstance(author, dict) and author.get("name"):
        lines.append(f"<i>{discord_to_html(str(author['name']))}</i>")

    title = embed.get("title")
    if title:
        rendered = discord_to_html(str(title))
        url = embed.get("url")
        if url:
            rendered = discord_to_html(f"[{title}]({url})")
            lines.append(f"<b>{rendered}</b>" if "<a " not in rendered else rendered)
        else:
            lines.append(f"<b>{rendered}</b>")

    if description := embed.get("description"):
        lines.append(discord_to_html(str(description)))

    for field in (embed.get("fields") or [])[:MAX_FIELDS]:
        if not isinstance(field, dict):
            continue
        name = discord_to_html(str(field.get("name", "")))
        value = discord_to_html(str(field.get("value", "")))
        if name and value:
            lines.append(f"<b>{name}:</b> {value}")
        elif value:
            lines.append(value)

    footer = embed.get("footer")
    if isinstance(footer, dict) and footer.get("text"):
        lines.append(f"<i>{discord_to_html(str(footer['text']))}</i>")
    return lines


class DiscordAdapter:
    name = "discord"

    def verify(self, body: bytes, request: HookRequest, config: AppConfig) -> None:
        return None

    def render(self, payload: Any, request: HookRequest, config: AppConfig) -> HookMessage:
        if not isinstance(payload, dict):
            raise HookError(422, "invalid_payload", "Expected a Discord webhook JSON object.")

        lines: list[str] = []
        content = payload.get("content")
        if content:
            lines.append(discord_to_html(str(content)))

        level: Level | None = None
        for embed in (payload.get("embeds") or [])[:MAX_EMBEDS]:
            if not isinstance(embed, dict):
                continue
            level = level or _level(embed.get("color"))
            lines.extend(_embed(embed))

        # The sender's name only helps as a heading for actual content.
        username = payload.get("username")
        if username and not content and any(line.strip() for line in lines):
            lines.insert(0, f"<b>{discord_to_html(str(username))}</b>")

        message = "\n".join(line for line in lines if line.strip())
        if not message:
            raise HookError(422, "empty_message", "The payload carries no content or embeds.")
        return HookMessage(text=message, level=level)
