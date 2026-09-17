"""Background delivery worker.

* Polls SQLite for due messages (and wakes up immediately on `notify()`).
* One lane (task + queue) per chat keeps per-chat ordering and isolates slow chats.
* Delivery is at-least-once: a crash mid-send may repeat the in-flight message.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from topicast import metrics
from topicast.config import AppConfig, Settings
from topicast.db import Database, Message, MessageStatus
from topicast.db.models import FINAL_STATUSES, utcnow
from topicast.delivery.formatting import ParseMode
from topicast.delivery.ratelimit import RateLimiter, bot_key, chat_key
from topicast.delivery.service import (
    MessageService,
    keyboard_of,
    media_items,
    spool_files,
    target_for,
)
from topicast.delivery.telegram import DeliveryError, Target, TelegramGateway

log = structlog.get_logger(__name__)

CLAIM_BATCH = 50
HOUSEKEEPING_INTERVAL = 30.0
MAX_BACKOFF_SECONDS = 300.0
ORPHAN_FILE_AGE = timedelta(hours=24)

Step = Callable[[ParseMode | None], Awaitable[list[int]]]


def backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter: ~2s, 4s, 8s … capped at 5 minutes."""
    base = min(MAX_BACKOFF_SECONDS, 2.0**attempt)
    return random.uniform(base / 2, base)  # noqa: S311 - jitter, not crypto


class DeliveryWorker:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        settings: Settings,
        gateway: TelegramGateway,
        limiter: RateLimiter,
        service: MessageService,
    ) -> None:
        self.db = db
        self.config = config
        self.settings = settings
        self.gateway = gateway
        self.limiter = limiter
        self.service = service
        self._wakeup = asyncio.Event()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lanes: dict[str, asyncio.Queue[str]] = {}
        self._lane_tasks: dict[str, asyncio.Task[None]] = {}
        self._waiters: dict[str, list[asyncio.Future[Message]]] = {}
        self._last_housekeeping = 0.0
        self.running = False

    # -------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        async with self.db.session() as session:
            await session.execute(
                update(Message)
                .where(Message.status == MessageStatus.SENDING)
                .values(status=MessageStatus.QUEUED)
            )
            await session.commit()
        self._task = asyncio.create_task(self._run(), name="topicast-worker")
        self.running = True

    async def stop(self) -> None:
        self._stopping.set()
        self._wakeup.set()
        tasks = [t for t in (self._task, *self._lane_tasks.values()) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.running = False

    def notify(self) -> None:
        self._wakeup.set()

    async def wait_for(self, message_id: str, timeout: float) -> Message | None:
        """Wait until `message_id` reaches a final status. None on timeout."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        self._waiters.setdefault(message_id, []).append(future)
        try:
            current = await self.service.get(message_id)
            if current is not None and current.status in FINAL_STATUSES:
                return current
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return None
        finally:
            waiters = self._waiters.get(message_id, [])
            if future in waiters:
                waiters.remove(future)
            if not waiters:
                self._waiters.pop(message_id, None)

    def _resolve(self, message: Message) -> None:
        for future in self._waiters.pop(message.id, []):
            if not future.done():
                future.set_result(message)

    # ------------------------------------------------------------------- loop

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._housekeeping()
                await self._claim_due()
            except Exception:
                log.exception("worker_tick_failed")
            self._wakeup.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), self.settings.poll_interval)

    async def _claim_due(self) -> None:
        now = utcnow()
        async with self.db.session() as session:
            rows = (
                await session.execute(
                    select(Message.id, Message.chat)
                    .where(Message.status == MessageStatus.QUEUED, Message.next_attempt_at <= now)
                    .order_by(Message.created_at)
                    .limit(CLAIM_BATCH)
                )
            ).all()
            if rows:
                await session.execute(
                    update(Message)
                    .where(Message.id.in_([r.id for r in rows]))
                    .values(status=MessageStatus.SENDING, updated_at=now)
                )
                await session.commit()
        for message_id, chat in rows:
            self._lane(chat).put_nowait(message_id)

    def _lane(self, chat: str) -> asyncio.Queue[str]:
        queue = self._lanes.get(chat)
        if queue is None:
            queue = asyncio.Queue()
            self._lanes[chat] = queue
            self._lane_tasks[chat] = asyncio.create_task(
                self._consume(queue), name=f"topicast-lane-{chat}"
            )
        return queue

    async def _consume(self, queue: asyncio.Queue[str]) -> None:
        while True:
            message_id = await queue.get()
            try:
                await self.deliver(message_id)
            except Exception as exc:
                log.exception("delivery_crashed", message_id=message_id)
                await self._mark_crashed(message_id, exc)
            finally:
                queue.task_done()

    async def _mark_crashed(self, message_id: str, exc: Exception) -> None:
        try:
            async with self.db.session() as session:
                message = await session.get(Message, message_id)
                if message is not None and message.status == MessageStatus.SENDING:
                    await self._finish(session, message, error=f"internal error: {exc!r}")
        except Exception:
            log.exception("mark_crashed_failed", message_id=message_id)

    async def _housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_housekeeping < HOUSEKEEPING_INTERVAL:
            return
        self._last_housekeeping = now
        await self.service.flush_dedupe_windows()
        purged = await self.service.purge()
        if purged:
            log.info("retention_purged", messages=purged)
        await self._clean_spool()
        async with self.db.session() as session:
            depth = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.status.in_([MessageStatus.QUEUED, MessageStatus.SENDING]))
            )
        metrics.QUEUE_DEPTH.set(depth or 0)

    async def _clean_spool(self) -> None:
        spool = self.settings.spool_dir
        if not spool.is_dir():
            return
        async with self.db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(Message.payload).where(
                            Message.status.in_(
                                [MessageStatus.QUEUED, MessageStatus.SENDING, MessageStatus.FAILED]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
        referenced = {p.resolve() for payload in rows for p in spool_files(payload)}
        cutoff = time.time() - ORPHAN_FILE_AGE.total_seconds()
        for path in spool.iterdir():
            if not path.is_file() or path.resolve() in referenced:
                continue
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    # --------------------------------------------------------------- delivery

    def _steps(self, message: Message, target: Target) -> list[Step]:
        payload: dict[str, Any] = message.payload
        silent = bool(payload.get("silent"))
        disable_preview = bool(payload.get("disable_preview"))
        items = media_items(payload)
        caption: str | None = payload.get("caption")
        keyboard = keyboard_of(payload) or None  # None keeps the Bot API call clean
        steps: list[Step] = []

        if len(items) == 1:
            item = items[0]

            async def send_one(mode: ParseMode | None) -> list[int]:
                return [
                    await self.gateway.send_media(
                        target,
                        item,
                        caption,
                        parse_mode=mode,
                        silent=silent,
                        keyboard=keyboard if not payload.get("texts") else None,
                    )
                ]

            steps.append(send_one)
        elif items:

            async def send_group(mode: ParseMode | None) -> list[int]:
                return await self.gateway.send_media_group(
                    target, items, caption, parse_mode=mode, silent=silent
                )

            steps.append(send_group)

        texts: list[str] = payload.get("texts", [])
        for index, chunk in enumerate(texts):
            last = index == len(texts) - 1

            async def send_text(
                mode: ParseMode | None, chunk: str = chunk, last: bool = last
            ) -> list[int]:
                # Buttons ride on the final message, where the reader ends up.
                return [
                    await self.gateway.send_text(
                        target,
                        chunk,
                        parse_mode=mode,
                        silent=silent,
                        disable_preview=disable_preview,
                        keyboard=keyboard if last else None,
                    )
                ]

            steps.append(send_text)
        return steps

    async def deliver(self, message_id: str) -> None:
        async with self.db.session() as session:
            message = await session.get(Message, message_id)
            if message is None or message.status != MessageStatus.SENDING:
                return
            if message.alias not in self.config.aliases:
                await self._finish(session, message, error="alias is no longer configured")
                return

            chat_name, target = target_for(self.config, message.alias)
            parse_mode: ParseMode | None = message.payload.get("parse_mode")
            steps = self._steps(message, target)
            done = int(message.payload.get("steps_done", 0))
            ids = list(message.telegram_message_ids)
            try:
                for index in range(done, len(steps)):
                    await self.limiter.acquire(bot_key(target.bot), chat_key(chat_name))
                    ids += await self._run_step(message, steps[index], parse_mode)
                    message.telegram_message_ids = list(ids)
                    message.payload = {**message.payload, "steps_done": index + 1}
                    await session.commit()
            except DeliveryError as exc:
                await self._handle_failure(session, message, exc, chat_name)
                return
            await self._finish(session, message)

    async def _run_step(
        self, message: Message, step: Step, parse_mode: ParseMode | None
    ) -> list[int]:
        try:
            return await step(parse_mode)
        except DeliveryError as exc:
            if not (exc.parse_error and parse_mode):
                raise
            log.warning("markup_fallback", message_id=message.id, error=str(exc))
            metrics.MARKUP_FALLBACKS.labels(message.alias).inc()
            message.fallback_reason = str(exc)
            return await step(None)

    async def _handle_failure(
        self, session: AsyncSession, message: Message, exc: DeliveryError, chat_name: str
    ) -> None:
        now = utcnow()
        if exc.retry_after is not None:
            self.limiter.pause(chat_key(chat_name), exc.retry_after)
            message.status = MessageStatus.QUEUED
            message.next_attempt_at = now + timedelta(seconds=exc.retry_after)
            message.last_error = str(exc)
            metrics.TELEGRAM_RETRIES.labels("rate_limited").inc()
            log.warning("telegram_rate_limited", message_id=message.id, retry_after=exc.retry_after)
            await session.commit()
            return
        if exc.retryable and message.attempts + 1 < self.settings.max_attempts:
            message.attempts += 1
            delay = backoff_delay(message.attempts)
            message.status = MessageStatus.QUEUED
            message.next_attempt_at = now + timedelta(seconds=delay)
            message.last_error = str(exc)
            metrics.TELEGRAM_RETRIES.labels("transient").inc()
            log.warning(
                "delivery_retry",
                message_id=message.id,
                attempt=message.attempts,
                delay=round(delay, 2),
                error=str(exc),
            )
            await session.commit()
            return
        message.attempts += 1
        await self._finish(session, message, error=str(exc))

    async def _finish(
        self, session: AsyncSession, message: Message, *, error: str | None = None
    ) -> None:
        now = utcnow()
        if error is None:
            message.status = MessageStatus.DELIVERED
            message.delivered_at = now
            message.last_error = None
            metrics.MESSAGES_DELIVERED.labels(message.alias).inc()
            metrics.DELIVERY_LATENCY.observe((now - message.created_at).total_seconds())
            log.info(
                "message_delivered",
                message_id=message.id,
                alias=message.alias,
                telegram_message_ids=message.telegram_message_ids,
            )
        else:
            message.status = MessageStatus.FAILED
            message.last_error = error
            metrics.MESSAGES_FAILED.labels(message.alias).inc()
            log.error("message_failed", message_id=message.id, alias=message.alias, error=error)
        await session.commit()
        if error is None:
            for path in spool_files(message.payload):
                Path(path).unlink(missing_ok=True)  # noqa: ASYNC240
        self._resolve(message)
