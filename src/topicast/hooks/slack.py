"""Slack-compatible webhook.

Many self-hosted tools can only post to a Slack "Incoming Webhook". Point them here and their
payload lands in a Telegram topic instead — no code change on their side.
"""

from __future__ import annotations

from typing import Any

from topicast.config import AppConfig, Level
from topicast.hooks import HookError, HookMessage, HookRequest
from topicast.hooks.markup import slack_to_html

MAX_FIELDS = 10
MAX_ATTACHMENTS = 5

# Slack's documented colors, plus the hex shades tools commonly send.
_COLOR_LEVELS = {
    "good": Level.SUCCESS,
    "warning": Level.WARNING,
    "danger": Level.ERROR,
    "#2eb886": Level.SUCCESS,
    "#36a64f": Level.SUCCESS,
    "#daa038": Level.WARNING,
    "#ffcc00": Level.WARNING,
    "#a30200": Level.ERROR,
    "#ff0000": Level.ERROR,
}


def _level(color: object) -> Level | None:
    return _COLOR_LEVELS.get(str(color).strip().lower()) if color else None


def _block_text(block: dict[str, Any]) -> str:
    """Pull the human readable text out of one Block Kit block."""
    text = block.get("text")
    if isinstance(text, dict):
        return str(text.get("text", ""))
    if isinstance(text, str):
        return text
    if block.get("type") == "context":
        elements = block.get("elements") or []
        return " ".join(
            str(e.get("text", "")) for e in elements if isinstance(e, dict) and e.get("text")
        )
    if block.get("type") == "divider":
        return "—"
    return ""


def _attachment(attachment: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if pretext := attachment.get("pretext"):
        lines.append(slack_to_html(str(pretext)))

    title = attachment.get("title")
    if title:
        rendered = slack_to_html(str(title))
        link = attachment.get("title_link")
        if link:
            rendered = slack_to_html(f"<{link}|{title}>")
        lines.append(f"<b>{rendered}</b>" if not link else rendered)

    body = attachment.get("text") or (attachment.get("fallback") if not title else None)
    if body:
        lines.append(slack_to_html(str(body)))

    for field in (attachment.get("fields") or [])[:MAX_FIELDS]:
        if not isinstance(field, dict):
            continue
        name = slack_to_html(str(field.get("title", "")))
        value = slack_to_html(str(field.get("value", "")))
        if name and value:
            lines.append(f"<b>{name}:</b> {value}")
        elif value:
            lines.append(value)

    footer = attachment.get("footer")
    if footer:
        lines.append(f"<i>{slack_to_html(str(footer))}</i>")
    return lines


class SlackAdapter:
    name = "slack"

    def verify(self, body: bytes, request: HookRequest, config: AppConfig) -> None:
        return None

    def render(self, payload: Any, request: HookRequest, config: AppConfig) -> HookMessage:
        if not isinstance(payload, dict):
            raise HookError(422, "invalid_payload", "Expected a Slack webhook JSON object.")

        lines: list[str] = []
        if text := payload.get("text"):
            lines.append(slack_to_html(str(text)))

        for block in payload.get("blocks") or []:
            if isinstance(block, dict) and (rendered := _block_text(block)):
                lines.append(slack_to_html(rendered))

        level: Level | None = None
        for attachment in (payload.get("attachments") or [])[:MAX_ATTACHMENTS]:
            if not isinstance(attachment, dict):
                continue
            level = level or _level(attachment.get("color"))
            lines.extend(_attachment(attachment))

        message = "\n".join(line for line in lines if line.strip())
        if not message:
            raise HookError(
                422, "empty_message", "The payload carries no text, blocks or attachments."
            )
        return HookMessage(text=message, level=level)
