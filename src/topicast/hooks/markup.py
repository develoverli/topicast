"""Convert Slack mrkdwn and Discord markdown into the HTML subset Telegram accepts.

Links are pulled out before escaping, so a label can never inject markup, and everything
that is not a recognised token ends up escaped.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from html import escape

# <https://example.com|label> or <https://example.com>
_SLACK_LINK = re.compile(r"<(?P<url>https?://[^>|\s]+)(?:\|(?P<label>[^>]*))?>")
# [label](https://example.com)
_DISCORD_LINK = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\"'>=])\bhttps?://[^\s<>()]+")

_PLACEHOLDER = "\x00{}\x00"
_CODE_BLOCK = re.compile(r"```(?:[a-z]*\n)?(.+?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_SLACK_BOLD = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_SLACK_ITALIC = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")
_DISCORD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_DISCORD_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_STRIKE = re.compile(r"~~?([^~\n]+)~~?")


def _protect(
    text: str, pattern: re.Pattern[str], render: Callable[[re.Match[str]], str], parts: list[str]
) -> str:
    def store(match: re.Match[str]) -> str:
        parts.append(render(match))
        return _PLACEHOLDER.format(len(parts) - 1)

    return pattern.sub(store, text)


def _link(url: str, label: str | None) -> str:
    text = escape(label or url, quote=False)
    return f'<a href="{escape(url, quote=True)}">{text}</a>'


def _restore(text: str, parts: list[str]) -> str:
    for index, value in enumerate(parts):
        text = text.replace(_PLACEHOLDER.format(index), value)
    return text


def _convert(
    text: str, link_pattern: re.Pattern[str], bold: re.Pattern[str], italic: re.Pattern[str]
) -> str:
    parts: list[str] = []
    text = _protect(
        text, link_pattern, lambda m: _link(m.group("url"), m.groupdict().get("label")), parts
    )
    text = _protect(
        text, _CODE_BLOCK, lambda m: f"<pre>{escape(m.group(1).strip(), quote=False)}</pre>", parts
    )
    text = _protect(
        text, _INLINE_CODE, lambda m: f"<code>{escape(m.group(1), quote=False)}</code>", parts
    )
    text = _protect(text, _BARE_URL, lambda m: _link(m.group(0), None), parts)

    text = escape(text, quote=False)
    text = bold.sub(r"<b>\1</b>", text)
    text = italic.sub(r"<i>\1</i>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    return _restore(text, parts)


def slack_to_html(text: str) -> str:
    """`*bold*`, `_italic_`, `` `code` ``, `<url|label>`."""
    return _convert(text, _SLACK_LINK, _SLACK_BOLD, _SLACK_ITALIC)


def discord_to_html(text: str) -> str:
    """`**bold**`, `*italic*`, `` `code` ``, `[label](url)`, `~~strike~~`."""
    return _convert(text, _DISCORD_LINK, _DISCORD_BOLD, _DISCORD_ITALIC)
