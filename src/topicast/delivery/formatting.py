"""Text shaping: level prefixes, escaping and Telegram length limits."""

from __future__ import annotations

import html
import re
from enum import StrEnum
from typing import Literal

from topicast.config import Level

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
ELLIPSIS = "…"

ParseMode = Literal["HTML", "MarkdownV2"]

_MDV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


class Overflow(StrEnum):
    SPLIT = "split"
    TRUNCATE = "truncate"
    REJECT = "reject"


class TextTooLongError(ValueError):
    def __init__(self, length: int, limit: int) -> None:
        super().__init__(f"text is {length} characters long; the limit is {limit}")
        self.length = length
        self.limit = limit


def escape(text: str, parse_mode: ParseMode | None) -> str:
    """Escape `text` so it renders literally under `parse_mode`."""
    if parse_mode == "HTML":
        return html.escape(text, quote=False)
    if parse_mode == "MarkdownV2":
        return _MDV2_SPECIAL.sub(r"\\\1", text)
    return text


def apply_level(
    text: str, level: Level | None, prefixes: dict[Level, str], parse_mode: ParseMode | None
) -> str:
    if level is None:
        return text
    prefix = prefixes.get(level, "")
    if not prefix:
        return text
    return f"{escape(prefix, parse_mode)} {text}" if text else escape(prefix, parse_mode)


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Split on paragraph, then line, then word boundaries; hard-cut as a last resort."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = -1
        for separator in ("\n\n", "\n", " "):
            idx = window.rfind(separator)
            if idx > limit // 4:
                cut = idx
                break
        if cut == -1:
            chunks.append(window)
            remaining = remaining[limit:]
            continue
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n ")
    if remaining:
        chunks.append(remaining)
    return [chunk for chunk in chunks if chunk]


def truncate(text: str, limit: int = TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(ELLIPSIS)] + ELLIPSIS


def fit(text: str, overflow: Overflow, limit: int = TEXT_LIMIT) -> list[str]:
    """Apply the overflow policy. Returns one or more chunks, each within `limit`."""
    if len(text) <= limit:
        return [text]
    if overflow is Overflow.REJECT:
        raise TextTooLongError(len(text), limit)
    if overflow is Overflow.TRUNCATE:
        return [truncate(text, limit)]
    return split_text(text, limit)


def fit_caption(caption: str, overflow: Overflow) -> tuple[str, list[str]]:
    """Return (caption, follow_up_texts) so the caption fits Telegram's caption limit."""
    if len(caption) <= CAPTION_LIMIT:
        return caption, []
    if overflow is Overflow.REJECT:
        raise TextTooLongError(len(caption), CAPTION_LIMIT)
    if overflow is Overflow.TRUNCATE:
        return truncate(caption, CAPTION_LIMIT), []
    head = split_text(caption, CAPTION_LIMIT)[0]
    rest = caption[len(head) :].lstrip("\n ")
    return head, split_text(rest) if rest else []
