"""Telegram Bot API gateway.

The rest of the app talks to `TelegramGateway` and only ever sees `DeliveryError`,
so python-telegram-bot stays an implementation detail (and tests can use a fake).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal, Protocol

from telegram import (
    Bot,
    InputMediaDocument,
    InputMediaPhoto,
    LinkPreviewOptions,
    Message,
)
from telegram.error import (
    BadRequest,
    ChatMigrated,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)

from topicast.delivery.formatting import ParseMode

MediaType = Literal["photo", "document"]

_PARSE_ERROR_MARKERS = ("can't parse", "parse entities", "unsupported start tag", "entity")
_NOT_MODIFIED_MARKER = "message is not modified"
_NOT_FOUND_MARKERS = (
    "message to delete not found",
    "message to edit not found",
    "message_id_invalid",
)


class DeliveryError(Exception):
    """A failed Bot API call, classified for the retry policy."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        parse_error: bool = False,
        not_found: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.parse_error = parse_error
        self.not_found = not_found


@dataclass(frozen=True, slots=True)
class MediaItem:
    type: MediaType
    url: str | None = None
    file: str | None = None
    filename: str | None = None

    @property
    def source(self) -> str | Path:
        if self.file is not None:
            return Path(self.file)
        if self.url is not None:
            return self.url
        msg = "media item has neither url nor file"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Target:
    bot: str
    chat_id: int | str
    thread_id: int | None


@dataclass(frozen=True, slots=True)
class BotIdentity:
    id: int
    username: str | None


class TelegramGateway(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def get_me(self, bot: str) -> BotIdentity: ...

    async def send_text(
        self,
        target: Target,
        text: str,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
        disable_preview: bool,
    ) -> int: ...

    async def send_media(
        self,
        target: Target,
        item: MediaItem,
        caption: str | None,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
    ) -> int: ...

    async def send_media_group(
        self,
        target: Target,
        items: Sequence[MediaItem],
        caption: str | None,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
    ) -> list[int]: ...

    async def edit_text(
        self,
        target: Target,
        message_id: int,
        text: str,
        *,
        parse_mode: ParseMode | None,
        caption: bool,
    ) -> None: ...

    async def delete(self, target: Target, message_ids: Sequence[int]) -> None: ...


def _seconds(value: int | timedelta) -> float:
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


def classify(exc: TelegramError) -> DeliveryError:
    """Map python-telegram-bot exceptions to a retry decision."""
    text = str(exc)
    lowered = text.lower()
    if isinstance(exc, RetryAfter):
        return DeliveryError(text, retryable=True, retry_after=_seconds(exc.retry_after))
    if isinstance(exc, ChatMigrated):
        return DeliveryError(
            f"chat was upgraded to a supergroup; update the chat id to {exc.new_chat_id}"
        )
    if isinstance(exc, InvalidToken | Forbidden):
        return DeliveryError(text)
    if isinstance(exc, BadRequest):
        return DeliveryError(
            text,
            parse_error=any(marker in lowered for marker in _PARSE_ERROR_MARKERS),
            not_found=any(marker in lowered for marker in _NOT_FOUND_MARKERS),
        )
    if isinstance(exc, TimedOut | NetworkError):
        return DeliveryError(text, retryable=True)
    return DeliveryError(text)


class PTBGateway:
    """`TelegramGateway` backed by python-telegram-bot."""

    def __init__(self, tokens: dict[str, str]) -> None:
        self._bots = {name: Bot(token=token) for name, token in tokens.items()}

    def _bot(self, name: str) -> Bot:
        return self._bots[name]

    async def start(self) -> None:
        for bot in self._bots.values():
            try:
                await bot.initialize()
            except TelegramError as exc:
                raise classify(exc) from exc

    async def close(self) -> None:
        for bot in self._bots.values():
            try:
                await bot.shutdown()
            except TelegramError:  # pragma: no cover - best effort on shutdown
                continue

    async def get_me(self, bot: str) -> BotIdentity:
        try:
            me = await self._bot(bot).get_me()
        except TelegramError as exc:
            raise classify(exc) from exc
        return BotIdentity(id=me.id, username=me.username)

    async def send_text(
        self,
        target: Target,
        text: str,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
        disable_preview: bool,
    ) -> int:
        try:
            msg = await self._bot(target.bot).send_message(
                chat_id=target.chat_id,
                text=text,
                message_thread_id=target.thread_id,
                parse_mode=parse_mode,
                disable_notification=silent,
                link_preview_options=LinkPreviewOptions(is_disabled=disable_preview),
            )
        except TelegramError as exc:
            raise classify(exc) from exc
        return msg.message_id

    async def send_media(
        self,
        target: Target,
        item: MediaItem,
        caption: str | None,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
    ) -> int:
        bot = self._bot(target.bot)
        msg: Message
        try:
            if item.type == "photo":
                msg = await bot.send_photo(
                    chat_id=target.chat_id,
                    photo=item.source,
                    caption=caption,
                    message_thread_id=target.thread_id,
                    parse_mode=parse_mode,
                    disable_notification=silent,
                )
            else:
                msg = await bot.send_document(
                    chat_id=target.chat_id,
                    document=item.source,
                    filename=item.filename,
                    caption=caption,
                    message_thread_id=target.thread_id,
                    parse_mode=parse_mode,
                    disable_notification=silent,
                )
        except TelegramError as exc:
            raise classify(exc) from exc
        return msg.message_id

    async def send_media_group(
        self,
        target: Target,
        items: Sequence[MediaItem],
        caption: str | None,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
    ) -> list[int]:
        media: list[InputMediaPhoto | InputMediaDocument] = []
        for index, item in enumerate(items):
            item_caption = caption if index == 0 else None
            item_mode = parse_mode if index == 0 else None
            if item.type == "photo":
                media.append(
                    InputMediaPhoto(item.source, caption=item_caption, parse_mode=item_mode)
                )
            else:
                media.append(
                    InputMediaDocument(
                        item.source,
                        caption=item_caption,
                        parse_mode=item_mode,
                        filename=item.filename,
                    )
                )
        try:
            messages = await self._bot(target.bot).send_media_group(
                chat_id=target.chat_id,
                media=media,
                message_thread_id=target.thread_id,
                disable_notification=silent,
            )
        except TelegramError as exc:
            raise classify(exc) from exc
        return [m.message_id for m in messages]

    async def edit_text(
        self,
        target: Target,
        message_id: int,
        text: str,
        *,
        parse_mode: ParseMode | None,
        caption: bool,
    ) -> None:
        bot = self._bot(target.bot)
        try:
            if caption:
                await bot.edit_message_caption(
                    chat_id=target.chat_id,
                    message_id=message_id,
                    caption=text,
                    parse_mode=parse_mode,
                )
            else:
                await bot.edit_message_text(
                    chat_id=target.chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=parse_mode,
                )
        except TelegramError as exc:
            if _NOT_MODIFIED_MARKER in str(exc).lower():
                return
            raise classify(exc) from exc

    async def delete(self, target: Target, message_ids: Sequence[int]) -> None:
        try:
            await self._bot(target.bot).delete_messages(
                chat_id=target.chat_id, message_ids=list(message_ids)
            )
        except TelegramError as exc:
            raise classify(exc) from exc
