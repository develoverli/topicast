"""Check a deployment against the real Telegram API.

`check-config` only proves the YAML parses. This asks Telegram whether the token works, the
bot is in the chat, it may post there, and — with `probe=True` — whether each alias actually
delivers, by sending a message and deleting it again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from sqlalchemy import func, select

from topicast.config import AppConfig, Settings
from topicast.db import ApiKey, Database
from topicast.delivery.service import target_for
from topicast.delivery.telegram import DeliveryError, TelegramGateway

PROBE_TEXT = "topicast doctor: delivery works. This message deletes itself."


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Check:
    status: Status
    name: str
    detail: str
    hint: str | None = None

    @classmethod
    def ok(cls, name: str, detail: str) -> Self:
        return cls(Status.OK, name, detail)

    @classmethod
    def warn(cls, name: str, detail: str, hint: str | None = None) -> Self:
        return cls(Status.WARN, name, detail, hint)

    @classmethod
    def fail(cls, name: str, detail: str, hint: str | None = None) -> Self:
        return cls(Status.FAIL, name, detail, hint)


@dataclass(frozen=True, slots=True)
class Report:
    checks: list[Check]

    @property
    def failed(self) -> bool:
        return any(check.status is Status.FAIL for check in self.checks)

    @property
    def counts(self) -> dict[Status, int]:
        return {status: sum(1 for c in self.checks if c.status is status) for status in Status}


async def _storage_checks(settings: Settings, db: Database) -> list[Check]:
    checks: list[Check] = []
    try:
        probe = settings.data_dir / ".doctor"
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check.ok("storage", f"{settings.data_dir} is writable"))
    except (OSError, ValueError) as exc:  # a bad path must be reported, never crash the command
        checks.append(
            Check.fail(
                "storage",
                f"cannot write to {settings.data_dir}: {exc}",
                "Check the volume mount and its ownership (the image runs as uid 10001).",
            )
        )

    try:
        await db.ping()
    except Exception as exc:  # any driver error means the same thing for the operator
        return [
            *checks,
            Check.fail(
                "database",
                f"cannot open {settings.database_path}: {exc}",
                "Run `topicast migrate`, or check that only one instance uses this volume.",
            ),
        ]

    async with db.session() as session:
        active = await session.scalar(
            select(func.count()).select_from(ApiKey).where(ApiKey.revoked_at.is_(None))
        )
    checks.append(Check.ok("database", f"{settings.database_path} is reachable"))
    if active:
        checks.append(Check.ok("api keys", f"{active} active"))
    else:
        checks.append(
            Check.warn(
                "api keys",
                "no active keys",
                "Create one: topicast keys create <service> --scope send --alias <alias>",
            )
        )
    return checks


async def _bot_checks(
    config: AppConfig, gateway: TelegramGateway
) -> tuple[list[Check], dict[str, int]]:
    checks: list[Check] = []
    bot_ids: dict[str, int] = {}
    for name in config.bots:
        try:
            me = await gateway.get_me(name)
        except DeliveryError as exc:
            checks.append(
                Check.fail(
                    f"bot '{name}'",
                    str(exc),
                    "Check the token in your environment; @BotFather can issue a new one.",
                )
            )
            continue
        bot_ids[name] = me.id
        checks.append(Check.ok(f"bot '{name}'", f"@{me.username} (id {me.id})"))
    return checks, bot_ids


async def _chat_checks(
    config: AppConfig, gateway: TelegramGateway, bot_ids: dict[str, int]
) -> list[Check]:
    checks: list[Check] = []
    for name, chat in config.chats.items():
        bot_id = bot_ids.get(chat.bot)
        if bot_id is None:
            checks.append(Check.fail(f"chat '{name}'", f"bot '{chat.bot}' is unavailable"))
            continue
        try:
            info = await gateway.get_chat(chat.bot, chat.id)
        except DeliveryError as exc:
            checks.append(
                Check.fail(
                    f"chat '{name}'",
                    str(exc),
                    "Wrong chat id, or the bot was never added to that group.",
                )
            )
            continue
        checks.append(Check.ok(f"chat '{name}'", f"{info.title or info.id} ({info.type})"))

        uses_topics = any(
            alias.chat == name and alias.topic is not None for alias in config.aliases.values()
        )
        if uses_topics and not info.is_forum:
            checks.append(
                Check.fail(
                    f"chat '{name}' topics",
                    "aliases point at topics but the group is not a forum",
                    "Enable Topics in the group settings, then copy the topic ids again.",
                )
            )

        try:
            member = await gateway.get_member(chat.bot, chat.id, bot_id)
        except DeliveryError as exc:
            checks.append(Check.fail(f"chat '{name}' membership", str(exc)))
            continue
        if not member.is_member:
            checks.append(
                Check.fail(
                    f"chat '{name}' membership",
                    f"the bot is '{member.status}' here",
                    "Add the bot back to the group.",
                )
            )
        elif not member.is_admin:
            checks.append(
                Check.warn(
                    f"chat '{name}' membership",
                    f"the bot is '{member.status}', not an admin",
                    "Posting to topics usually requires admin rights.",
                )
            )
        else:
            checks.append(Check.ok(f"chat '{name}' membership", f"bot is {member.status}"))
    return checks


async def _probe_checks(config: AppConfig, gateway: TelegramGateway) -> list[Check]:
    """Send a real message to every alias, then delete it. The only true end-to-end test."""
    checks: list[Check] = []
    for alias in config.aliases:
        _, target = target_for(config, alias)
        try:
            message_id = await gateway.send_text(
                target, PROBE_TEXT, parse_mode=None, silent=True, disable_preview=True
            )
        except DeliveryError as exc:
            checks.append(
                Check.fail(
                    f"alias '{alias}'",
                    str(exc),
                    "Usually a wrong topic id, a closed topic, or missing permissions.",
                )
            )
            continue
        try:
            await gateway.delete(target, [message_id])
            checks.append(Check.ok(f"alias '{alias}'", "delivered and cleaned up"))
        except DeliveryError as exc:
            checks.append(
                Check.warn(
                    f"alias '{alias}'",
                    f"delivered, but the probe could not be deleted: {exc}",
                    "Delete it by hand; the bot needs 'delete messages' rights.",
                )
            )
    return checks


async def run_doctor(
    settings: Settings,
    config: AppConfig,
    gateway: TelegramGateway,
    db: Database,
    *,
    probe: bool = False,
) -> Report:
    checks = await _storage_checks(settings, db)
    bot_checks, bot_ids = await _bot_checks(config, gateway)
    checks.extend(bot_checks)
    if bot_ids:
        checks.extend(await _chat_checks(config, gateway, bot_ids))
        if probe:
            checks.extend(await _probe_checks(config, gateway))
    elif probe:
        checks.append(Check.fail("probe", "skipped: no bot could be reached"))
    return Report(checks)


def config_path_hint(settings: Settings) -> Path:
    return settings.config_file
