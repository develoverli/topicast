from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeGateway, make_config
from topicast.config import AliasConfig, Settings
from topicast.db import Database, run_migrations
from topicast.delivery.telegram import ChatInfo, DeliveryError, MemberInfo
from topicast.doctor import Report, Status, run_doctor
from topicast.security import ALL_ALIASES, Scope


@pytest.fixture
async def db(settings: Settings) -> Database:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    run_migrations(settings.database_path)
    return Database(settings.database_path)


async def report_for(
    settings: Settings, gateway: FakeGateway, db: Database, **overrides: Any
) -> Report:
    return await run_doctor(settings, make_config(**overrides), gateway, db, probe=False)


def status_of(report: Report, prefix: str) -> Status:
    return next(check.status for check in report.checks if check.name.startswith(prefix))


def names(report: Report) -> list[str]:
    return [check.name for check in report.checks]


async def test_healthy_deployment(settings: Settings, gateway: FakeGateway, db: Database) -> None:
    report = await report_for(settings, gateway, db)
    assert not report.failed
    assert status_of(report, "storage") is Status.OK
    assert status_of(report, "database") is Status.OK
    assert status_of(report, "bot 'default'") is Status.OK
    assert status_of(report, "chat 'homelab'") is Status.OK


async def test_missing_keys_is_a_warning(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    report = await report_for(settings, gateway, db)
    assert status_of(report, "api keys") is Status.WARN
    assert not report.failed


async def test_existing_keys_pass(settings: Settings, gateway: FakeGateway, db: Database) -> None:
    from topicast.keys import KeyStore

    await KeyStore(db, settings.secret_key.get_secret_value()).create(
        "svc", [Scope.SEND], [ALL_ALIASES]
    )
    report = await report_for(settings, gateway, db)
    assert status_of(report, "api keys") is Status.OK


async def test_bad_token_fails_and_skips_chats(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    gateway.bot_error = DeliveryError("Unauthorized")
    report = await report_for(settings, gateway, db)
    assert report.failed
    assert status_of(report, "bot 'default'") is Status.FAIL
    assert not any(name.startswith("chat ") for name in names(report))


async def test_unreachable_chat_fails(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    gateway.chat_error = DeliveryError("Chat not found")
    report = await report_for(settings, gateway, db)
    assert report.failed
    assert status_of(report, "chat 'homelab'") is Status.FAIL


async def test_group_without_topics_fails_when_aliases_use_them(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    gateway.chat_info = ChatInfo(id=-100, type="supergroup", title="plain", is_forum=False)
    report = await report_for(settings, gateway, db)
    assert report.failed
    assert status_of(report, "chat 'homelab' topics") is Status.FAIL


async def test_group_without_topics_is_fine_without_topic_aliases(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    gateway.chat_info = ChatInfo(id=-100, type="supergroup", title="plain", is_forum=False)
    report = await report_for(
        settings, gateway, db, aliases={"general": AliasConfig(chat="homelab")}
    )
    assert not report.failed


async def test_non_admin_bot_is_a_warning(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    gateway.member_info = MemberInfo(status="member", can_post=True)
    report = await report_for(settings, gateway, db)
    assert status_of(report, "chat 'homelab' membership") is Status.WARN
    assert not report.failed


async def test_bot_kicked_out_fails(settings: Settings, gateway: FakeGateway, db: Database) -> None:
    gateway.member_info = MemberInfo(status="left", can_post=False)
    report = await report_for(settings, gateway, db)
    assert report.failed
    assert status_of(report, "chat 'homelab' membership") is Status.FAIL


async def test_probe_sends_and_deletes_a_message(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    report = await run_doctor(settings, make_config(), gateway, db, probe=True)
    assert not report.failed
    assert len(gateway.sent) == 2  # one per alias
    assert len(gateway.deleted) == 2
    assert status_of(report, "alias 'alerts'") is Status.OK


async def test_probe_reports_a_bad_topic(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    gateway.errors.append(DeliveryError("Bad Request: message thread not found"))
    report = await run_doctor(settings, make_config(), gateway, db, probe=True)
    assert report.failed
    assert status_of(report, "alias 'alerts'") is Status.FAIL


async def test_unwritable_data_dir_fails(
    settings: Settings, gateway: FakeGateway, db: Database, tmp_path: Path
) -> None:
    settings.data_dir = tmp_path / "nope" / "\0bad"
    report = await report_for(settings, gateway, db)
    assert report.failed
    assert status_of(report, "storage") is Status.FAIL
