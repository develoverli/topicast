"""Message lifecycle: accept → dedupe/idempotency → queue; edit; delete; retry."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from topicast import metrics
from topicast.config import AppConfig, Level, Settings
from topicast.db import Database, DedupeWindow, Message, MessageStatus
from topicast.db.models import utcnow
from topicast.delivery import formatting
from topicast.delivery.formatting import CAPTION_LIMIT, Overflow, ParseMode
from topicast.delivery.ratelimit import RateLimiter, bot_key, chat_key
from topicast.delivery.telegram import DeliveryError, MediaItem, Target, TelegramGateway

log = structlog.get_logger(__name__)

MAX_GROUP_ITEMS = 10
PREVIEW_CHARS = 200

Outcome = Literal["queued", "deduplicated", "existing"]


class ServiceError(Exception):
    """Business rule violation, mapped to an HTTP problem by the API layer."""

    def __init__(
        self, status: int, code: str, detail: str, *, retry_after: float | None = None
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.retry_after = retry_after


@dataclass(slots=True)
class OutgoingMessage:
    alias: str
    text: str | None = None
    parse_mode: ParseMode | None = None
    level: Level | None = None
    silent: bool | None = None
    disable_preview: bool = False
    dedupe_key: str | None = None
    overflow: Overflow = Overflow.SPLIT
    media: list[MediaItem] = field(default_factory=list)
    media_digests: list[str] = field(default_factory=list)
    send_at: datetime | None = None
    source: str = "api"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    outcome: Outcome
    message: Message


def new_message_id() -> str:
    return uuid.uuid4().hex


def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def target_for(config: AppConfig, alias: str) -> tuple[str, Target]:
    chat_name, chat = config.chat_for(alias)
    return chat_name, Target(bot=chat.bot, chat_id=chat.id, thread_id=config.aliases[alias].topic)


def media_items(payload: dict[str, Any]) -> list[MediaItem]:
    return [MediaItem(**item) for item in payload.get("media", [])]


def spool_files(payload: dict[str, Any]) -> list[Path]:
    return [Path(item["file"]) for item in payload.get("media", []) if item.get("file")]


class MessageService:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        settings: Settings,
        gateway: TelegramGateway,
        limiter: RateLimiter,
        notify: Callable[[], None] = lambda: None,
    ) -> None:
        self.db = db
        self.config = config
        self.settings = settings
        self.gateway = gateway
        self.limiter = limiter
        self.notify = notify
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ build

    def _kind(self, out: OutgoingMessage) -> str:
        if not out.media:
            if not out.text:
                raise ServiceError(422, "empty_message", "Provide `text` or at least one file.")
            return "text"
        if len(out.media) == 1:
            return out.media[0].type
        if len(out.media) > MAX_GROUP_ITEMS:
            raise ServiceError(
                422, "too_many_files", f"A media group holds at most {MAX_GROUP_ITEMS} items."
            )
        if len({item.type for item in out.media}) > 1:
            raise ServiceError(
                422, "mixed_media_group", "Photos and documents cannot be mixed in one group."
            )
        return "media_group"

    def build_payload(self, out: OutgoingMessage) -> tuple[str, dict[str, Any]]:
        if out.alias not in self.config.aliases:
            raise ServiceError(404, "unknown_alias", f"Alias '{out.alias}' is not configured.")
        kind = self._kind(out)
        text = formatting.apply_level(
            out.text or "", out.level, self.config.defaults.level_prefix, out.parse_mode
        )
        silent = out.silent
        if silent is None:
            silent = self._is_silent(out.alias, out.level)

        caption: str | None = None
        try:
            if kind == "text":
                texts = formatting.fit(text, out.overflow)
            else:
                caption, texts = formatting.fit_caption(text, out.overflow) if text else ("", [])
        except formatting.TextTooLongError as exc:
            raise ServiceError(422, "text_too_long", str(exc)) from exc

        payload: dict[str, Any] = {
            "texts": texts,
            "caption": caption or None,
            "media": [
                {
                    k: v
                    for k, v in (
                        ("type", m.type),
                        ("url", m.url),
                        ("file", m.file),
                        ("filename", m.filename),
                    )
                    if v is not None
                }
                for m in out.media
            ],
            "parse_mode": out.parse_mode,
            "silent": silent,
            "disable_preview": out.disable_preview,
            "preview": text[:PREVIEW_CHARS],
            "steps_done": 0,
        }
        return kind, payload

    def _is_silent(self, alias: str, level: Level | None) -> bool:
        """Quiet hours win over the level, except for the levels declared exempt."""
        config = self.config
        if config.in_quiet_hours(alias) and (
            level is None or level not in config.defaults.quiet_exempt_levels
        ):
            return True
        return level is not None and level in config.silent_levels(alias)

    def _fingerprint(self, out: OutgoingMessage, kind: str) -> str:
        if out.dedupe_key:
            return _sha256(["key", out.alias, out.dedupe_key])
        media = [m.url for m in out.media if m.url] + out.media_digests
        return _sha256(["content", out.alias, kind, out.text, out.level, media])

    def _request_hash(self, out: OutgoingMessage) -> str:
        return _sha256(
            [
                out.send_at,
                out.alias,
                out.text,
                out.parse_mode,
                out.level,
                out.silent,
                out.disable_preview,
                out.dedupe_key,
                out.overflow,
                [m.url for m in out.media],
                out.media_digests,
            ]
        )

    # ---------------------------------------------------------------- enqueue

    async def enqueue(
        self,
        out: OutgoingMessage,
        *,
        key_id: str | None,
        idempotency_key: str | None = None,
    ) -> EnqueueResult:
        kind, payload = self.build_payload(out)
        request_hash = self._request_hash(out)
        now = utcnow()
        async with self._lock, self.db.session() as session:
            if idempotency_key:
                existing = await self._find_idempotent(session, key_id, idempotency_key, now)
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise ServiceError(
                            409,
                            "idempotency_conflict",
                            "This Idempotency-Key was already used with a different request.",
                        )
                    return EnqueueResult("existing", existing)

            window = self.config.dedupe_window(out.alias)
            fingerprint = self._fingerprint(out, kind)
            if window > 0:
                row = await session.get(DedupeWindow, fingerprint)
                if row is not None and row.expires_at > now:
                    original = await session.get(Message, row.message_id)
                    if original is not None:
                        row.suppressed += 1
                        await session.commit()
                        metrics.MESSAGES_DEDUPLICATED.labels(out.alias).inc()
                        return EnqueueResult("deduplicated", original)
                if row is not None:
                    await self._close_window(session, row)
                    await session.flush()

            chat_name, _ = self.config.chat_for(out.alias)
            message = Message(
                id=new_message_id(),
                key_id=key_id,
                source=out.source,
                alias=out.alias,
                chat=chat_name,
                kind=kind,
                payload=payload,
                status=MessageStatus.QUEUED,
                attempts=0,
                next_attempt_at=out.send_at or now,
                telegram_message_ids=[],
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                created_at=now,
                updated_at=now,
            )
            session.add(message)
            if window > 0:
                session.add(
                    DedupeWindow(
                        fingerprint=fingerprint,
                        alias=out.alias,
                        message_id=message.id,
                        preview=payload["preview"],
                        suppressed=0,
                        expires_at=now + timedelta(seconds=window),
                    )
                )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ServiceError(
                    409, "conflict", "Concurrent request with the same Idempotency-Key."
                ) from exc

        metrics.MESSAGES_ACCEPTED.labels(out.alias, out.source).inc()
        log.info(
            "message_accepted",
            message_id=message.id,
            alias=out.alias,
            kind=kind,
            source=out.source,
            key_id=key_id,
        )
        self.notify()
        return EnqueueResult("queued", message)

    async def _find_idempotent(
        self, session: AsyncSession, key_id: str | None, idempotency_key: str, now: datetime
    ) -> Message | None:
        cutoff = now - timedelta(hours=self.settings.idempotency_hours)
        stmt = select(Message).where(
            Message.key_id == key_id,
            Message.idempotency_key == idempotency_key,
            Message.created_at >= cutoff,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _close_window(self, session: AsyncSession, row: DedupeWindow) -> None:
        """Delete an expired window, queueing a "repeated N×" summary when needed."""
        if row.suppressed > 0 and row.alias in self.config.aliases:
            window = self.config.dedupe_window(row.alias)
            chat_name, _ = self.config.chat_for(row.alias)
            text = f"🔁 Repeated {row.suppressed}× in the last {window}s:\n{row.preview}"
            now = utcnow()
            session.add(
                Message(
                    id=new_message_id(),
                    key_id=None,
                    source="dedupe",
                    alias=row.alias,
                    chat=chat_name,
                    kind="text",
                    payload={
                        "texts": formatting.split_text(text),
                        "caption": None,
                        "media": [],
                        "parse_mode": None,
                        "silent": True,
                        "disable_preview": True,
                        "preview": text[:PREVIEW_CHARS],
                        "steps_done": 0,
                    },
                    status=MessageStatus.QUEUED,
                    attempts=0,
                    next_attempt_at=now,
                    telegram_message_ids=[],
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.delete(row)

    async def flush_dedupe_windows(self) -> int:
        """Close every expired window. Returns how many summaries were queued."""
        now = utcnow()
        async with self._lock, self.db.session() as session:
            rows = (
                (await session.execute(select(DedupeWindow).where(DedupeWindow.expires_at <= now)))
                .scalars()
                .all()
            )
            summaries = sum(1 for r in rows if r.suppressed > 0)
            for row in rows:
                await self._close_window(session, row)
            await session.commit()
        if summaries:
            self.notify()
        return summaries

    # ------------------------------------------------------------- read/admin

    async def get(self, message_id: str) -> Message | None:
        async with self.db.session() as session:
            return await session.get(Message, message_id)

    async def list_messages(
        self, *, status: MessageStatus | None = None, limit: int = 50
    ) -> list[Message]:
        stmt = select(Message).order_by(Message.created_at.desc()).limit(limit)
        if status is not None:
            stmt = stmt.where(Message.status == status)
        async with self.db.session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def retry(self, message_id: str) -> Message:
        async with self.db.session() as session:
            message = await session.get(Message, message_id)
            if message is None:
                raise ServiceError(404, "message_not_found", "Message not found.")
            if message.status != MessageStatus.FAILED:
                raise ServiceError(409, "not_failed", "Only failed messages can be retried.")
            missing = [p for p in spool_files(message.payload) if not p.exists()]
            if missing:
                raise ServiceError(
                    409, "files_gone", "Uploaded files were already removed; send it again."
                )
            message.status = MessageStatus.QUEUED
            message.attempts = 0
            message.last_error = None
            message.next_attempt_at = utcnow()
            await session.commit()
        self.notify()
        return message

    async def purge(self) -> int:
        """Apply retention: drop old final messages, forget old idempotency keys."""
        now = utcnow()
        retention_cutoff = now - timedelta(days=self.settings.retention_days)
        idem_cutoff = now - timedelta(hours=self.settings.idempotency_hours)
        async with self.db.session() as session:
            result = await session.execute(
                delete(Message).where(
                    Message.status.in_(
                        [
                            s.value
                            for s in (
                                MessageStatus.DELIVERED,
                                MessageStatus.FAILED,
                                MessageStatus.DELETED,
                            )
                        ]
                    ),
                    Message.updated_at < retention_cutoff,
                )
            )
            await session.execute(
                update(Message)
                .where(Message.idempotency_key.is_not(None), Message.created_at < idem_cutoff)
                .values(idempotency_key=None)
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    # ------------------------------------------------------------ edit/delete

    async def edit(
        self,
        message_id: str,
        *,
        text: str,
        parse_mode: ParseMode | None,
        level: Level | None,
        overflow: Overflow,
    ) -> Message:
        async with self.db.session() as session:
            message = await session.get(Message, message_id)
            if message is None:
                raise ServiceError(404, "message_not_found", "Message not found.")
            if message.status != MessageStatus.DELIVERED or not message.telegram_message_ids:
                raise ServiceError(409, "not_delivered", "Only delivered messages can be edited.")
            if message.alias not in self.config.aliases:
                raise ServiceError(409, "unknown_alias", "The message's alias no longer exists.")
            is_caption = message.kind != "text"
            limit = CAPTION_LIMIT if is_caption else formatting.TEXT_LIMIT
            new_text = formatting.apply_level(
                text, level, self.config.defaults.level_prefix, parse_mode
            )
            if len(new_text) > limit:
                if overflow is not Overflow.TRUNCATE:
                    raise ServiceError(
                        422,
                        "text_too_long",
                        f"Edited text must fit in one message ({limit} characters); "
                        "use on_overflow=truncate to cut it.",
                    )
                new_text = formatting.truncate(new_text, limit)

            chat_name, target = target_for(self.config, message.alias)
            await self.limiter.acquire(bot_key(target.bot), chat_key(chat_name))
            try:
                await self.gateway.edit_text(
                    target,
                    message.telegram_message_ids[0],
                    new_text,
                    parse_mode=parse_mode,
                    caption=is_caption,
                )
            except DeliveryError as exc:
                raise _upstream(exc) from exc

            payload = dict(message.payload)
            payload["preview"] = new_text[:PREVIEW_CHARS]
            payload["parse_mode"] = parse_mode
            if is_caption:
                payload["caption"] = new_text
            else:
                payload["texts"] = [new_text, *payload["texts"][1:]]
            message.payload = payload
            await session.commit()
        log.info("message_edited", message_id=message_id)
        return message

    async def delete(self, message_id: str) -> Message:
        async with self.db.session() as session:
            message = await session.get(Message, message_id)
            if message is None:
                raise ServiceError(404, "message_not_found", "Message not found.")
            if message.status == MessageStatus.SENDING:
                raise ServiceError(
                    409, "sending", "The message is being delivered; retry in a moment."
                )
            if message.status == MessageStatus.DELETED:
                return message
            if message.telegram_message_ids and message.alias in self.config.aliases:
                chat_name, target = target_for(self.config, message.alias)
                await self.limiter.acquire(bot_key(target.bot), chat_key(chat_name))
                try:
                    await self.gateway.delete(target, message.telegram_message_ids)
                except DeliveryError as exc:
                    if not exc.not_found:
                        raise _upstream(exc) from exc
            message.status = MessageStatus.DELETED
            await session.commit()
        for path in spool_files(message.payload):
            path.unlink(missing_ok=True)
        log.info("message_deleted", message_id=message_id)
        return message


def _upstream(exc: DeliveryError) -> ServiceError:
    if exc.retry_after is not None:
        return ServiceError(429, "telegram_rate_limited", str(exc), retry_after=exc.retry_after)
    if exc.retryable:
        return ServiceError(502, "telegram_unavailable", str(exc))
    if exc.not_found:
        return ServiceError(404, "telegram_message_not_found", str(exc))
    return ServiceError(422, "telegram_rejected", str(exc))
