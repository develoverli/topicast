from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeGateway, make_config
from tests.test_transport import make_pair
from topicast.config import AliasConfig, GitHubHookConfig, HooksConfig, Settings
from topicast.db import Database, run_migrations
from topicast.delivery.telegram import ChatInfo, DeliveryError, MemberInfo
from topicast.doctor import Report, Status, exposure_checks, run_doctor
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


# --- exposure ---


def exposure(settings: Settings, **config_overrides: Any) -> Report:
    return Report(exposure_checks(settings, make_config(**config_overrides)))


def with_settings(settings: Settings, **changes: Any) -> Settings:
    return settings.model_copy(update=changes)


def test_loopback_default_is_private(settings: Settings) -> None:
    report = exposure(settings)
    assert status_of(report, "transport") is Status.OK
    assert status_of(report, "docs") is Status.OK
    assert status_of(report, "metrics") is Status.OK
    assert "tls certificate" not in names(report)


def test_plain_http_on_an_exposed_address_warns(settings: Settings) -> None:
    report = exposure(with_settings(settings, bind_address="0.0.0.0"))
    assert status_of(report, "transport") is Status.WARN
    assert status_of(report, "docs") is Status.WARN
    assert status_of(report, "metrics") is Status.WARN
    assert not report.failed


def test_tailscale_is_private(settings: Settings) -> None:
    report = exposure(with_settings(settings, bind_address="100.122.171.40"))
    assert status_of(report, "transport") is Status.OK
    assert status_of(report, "docs") is Status.OK


def test_proxy_is_encrypted_but_surfaces_stay_public(settings: Settings) -> None:
    report = exposure(with_settings(settings, bind_address="0.0.0.0", behind_proxy=True))
    assert status_of(report, "transport") is Status.OK
    assert status_of(report, "docs") is Status.WARN


def test_disabled_surfaces_pass_when_exposed(settings: Settings) -> None:
    changed = with_settings(
        settings, bind_address="0.0.0.0", docs_enabled=False, metrics_enabled=False
    )
    report = exposure(changed)
    assert status_of(report, "docs") is Status.OK
    assert status_of(report, "metrics") is Status.OK


def test_github_secret(settings: Settings) -> None:
    assert status_of(exposure(settings), "github secret") is Status.WARN
    real = HooksConfig(github=GitHubHookConfig(secret="s3cret"))
    assert status_of(exposure(settings, hooks=real), "github secret") is Status.OK


def tls_settings(settings: Settings, tmp_path: Path, key_from: str = "server") -> Settings:
    pair = make_pair(tmp_path / "tls")
    key = pair.key if key_from == "server" else make_pair(tmp_path / "other", "other").key
    return with_settings(settings, tls_cert_file=pair.cert, tls_key_file=key)


def tls_status(settings: Settings, now: datetime | None = None) -> Status:
    checks = exposure_checks(settings, make_config(), now=now)
    return status_of(Report(checks), "tls certificate")


def test_valid_certificate_passes(settings: Settings, tmp_path: Path) -> None:
    tls = tls_settings(settings, tmp_path)
    assert tls_status(tls) is Status.OK  # 30 days left
    assert status_of(exposure(tls), "transport") is Status.OK


def test_certificate_close_to_expiry_warns(settings: Settings, tmp_path: Path) -> None:
    soon = datetime.now(UTC) + timedelta(days=20)  # 10 days left
    assert tls_status(tls_settings(settings, tmp_path), now=soon) is Status.WARN


def test_expired_certificate_fails(settings: Settings, tmp_path: Path) -> None:
    later = datetime.now(UTC) + timedelta(days=60)
    assert tls_status(tls_settings(settings, tmp_path), now=later) is Status.FAIL


def test_mismatched_key_fails(settings: Settings, tmp_path: Path) -> None:
    assert tls_status(tls_settings(settings, tmp_path, key_from="other")) is Status.FAIL


def test_unreadable_expiry_warns(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("topicast.doctor.cert_expiry", lambda _path: None)
    assert tls_status(tls_settings(settings, tmp_path)) is Status.WARN


async def test_doctor_includes_exposure(
    settings: Settings, gateway: FakeGateway, db: Database
) -> None:
    report = await report_for(settings, gateway, db)
    assert "transport" in names(report)
    assert not report.failed
