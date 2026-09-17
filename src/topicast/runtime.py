"""Wires config, database, gateway, rate limiter, service and worker together."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from topicast.config import AppConfig, Settings, load_app_config
from topicast.db import Database, run_migrations
from topicast.delivery.ratelimit import RateLimiter, bot_key, chat_key
from topicast.delivery.service import MessageService
from topicast.delivery.telegram import DeliveryError, PTBGateway, TelegramGateway
from topicast.delivery.worker import DeliveryWorker
from topicast.keys import KeyStore

log = structlog.get_logger(__name__)

READY_CACHE_SECONDS = 60.0


def build_limiter(config: AppConfig) -> RateLimiter:
    limiter = RateLimiter()
    for name, bot in config.bots.items():
        limiter.configure(bot_key(name), bot.rate_per_second, 1.0)
    for name, chat in config.chats.items():
        limiter.configure(chat_key(name), chat.effective_rate_per_minute, 60.0)
    return limiter


@dataclass
class Runtime:
    settings: Settings
    config: AppConfig
    db: Database
    gateway: TelegramGateway
    limiter: RateLimiter
    keys: KeyStore
    service: MessageService
    worker: DeliveryWorker
    bot_status: dict[str, str] = field(default_factory=dict)
    _ready_checked_at: float = 0.0

    @classmethod
    def create(
        cls,
        settings: Settings,
        config: AppConfig | None = None,
        gateway: TelegramGateway | None = None,
    ) -> Runtime:
        config = config or load_app_config(settings.config_file)
        db = Database(settings.database_path)
        if gateway is None:
            gateway = PTBGateway(
                {name: bot.token.get_secret_value() for name, bot in config.bots.items()}
            )
        limiter = build_limiter(config)
        service = MessageService(db, config, settings, gateway, limiter)
        worker = DeliveryWorker(db, config, settings, gateway, limiter, service)
        service.notify = worker.notify
        keys = KeyStore(db, settings.secret_key.get_secret_value())
        return cls(settings, config, db, gateway, limiter, keys, service, worker)

    async def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings.spool_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(run_migrations, self.settings.database_path)
        try:
            await self.gateway.start()
        except DeliveryError as exc:
            # Keep serving: messages stay queued and /readyz reports the problem.
            log.error("telegram_init_failed", error=str(exc))
        await self.check_bots()
        await self.worker.start()
        log.info(
            "topicast_started",
            bots=len(self.config.bots),
            chats=len(self.config.chats),
            aliases=sorted(self.config.aliases),
        )

    async def stop(self) -> None:
        await self.worker.stop()
        await self.gateway.close()
        await self.db.dispose()
        log.info("topicast_stopped")

    async def check_bots(self, *, force: bool = True) -> dict[str, str]:
        now = time.monotonic()
        if not force and now - self._ready_checked_at < READY_CACHE_SECONDS:
            return self.bot_status
        status: dict[str, str] = {}
        for name in self.config.bots:
            try:
                me = await self.gateway.get_me(name)
            except DeliveryError as exc:
                status[name] = f"error: {exc}"
                log.error("bot_check_failed", bot=name, error=str(exc))
            else:
                status[name] = "ok"
                log.info("bot_ready", bot=name, username=me.username, bot_id=me.id)
        self.bot_status = status
        self._ready_checked_at = now
        return status
