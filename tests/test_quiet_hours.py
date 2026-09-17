from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.conftest import FakeGateway, make_config
from topicast.config import AliasConfig, Defaults, Level, QuietHours, load_app_config
from topicast.delivery.service import MessageService, OutgoingMessage
from topicast.runtime import Runtime


@pytest.mark.parametrize(
    ("window", "moment", "covered"),
    [
        ("23:00-08:00", "23:30", True),
        ("23:00-08:00", "03:00", True),
        ("23:00-08:00", "07:59", True),
        ("23:00-08:00", "08:00", False),
        ("23:00-08:00", "22:59", False),
        ("09:00-17:00", "12:00", True),
        ("09:00-17:00", "08:59", False),
        ("00:00-00:00", "12:00", False),  # empty window
    ],
)
def test_window_covers(window: str, moment: str, covered: bool) -> None:
    parsed = QuietHours.parse(window)
    assert parsed.covers(datetime.strptime(moment, "%H:%M").time()) is covered


def test_invalid_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid quiet_hours"):
        QuietHours.parse("11pm to 8am")


def test_config_reads_quiet_hours_and_timezone(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
bots: {default: {token: t}}
chats: {homelab: {id: -100}}
aliases:
  backups: {chat: homelab, topic: 9, quiet_hours: "23:00-08:00"}
defaults:
  timezone: Europe/Madrid
""",
        encoding="utf-8",
    )
    config = load_app_config(path, {})
    window = config.quiet_hours("backups")
    assert window is not None
    assert window.start.hour == 23
    # 01:00 UTC is 02:00 in Madrid (CET/CEST), inside the window either way.
    assert config.in_quiet_hours("backups", datetime(2026, 1, 15, 1, 0, tzinfo=UTC))
    # 09:00 UTC is 10:00 local, outside it.
    assert not config.in_quiet_hours("backups", datetime(2026, 1, 15, 9, 0, tzinfo=UTC))


def test_unknown_timezone_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
bots: {default: {token: t}}
chats: {homelab: {id: -100}}
aliases: {a: {chat: homelab}}
defaults: {timezone: Mars/Olympus}
""",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="not an IANA name"):
        load_app_config(path, {})


def test_alias_window_overrides_the_default() -> None:
    config = make_config(
        aliases={
            "loud": AliasConfig(chat="homelab", quiet_hours=QuietHours.parse("01:00-02:00")),
            "default-window": AliasConfig(chat="homelab"),
        },
        defaults=Defaults(quiet_hours=QuietHours.parse("23:00-08:00")),
    )
    night = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    assert not config.in_quiet_hours("loud", night)
    assert config.in_quiet_hours("default-window", night)


def _service(runtime: Runtime) -> MessageService:
    return runtime.service


async def test_messages_are_silent_during_quiet_hours(
    runtime: Runtime, gateway: FakeGateway
) -> None:
    runtime.service.config = make_config(
        defaults=Defaults(quiet_hours=QuietHours.parse("00:00-23:59"))  # always quiet
    )
    result = await runtime.service.enqueue(
        OutgoingMessage(alias="alerts", text="disk full", level=Level.ERROR), key_id=None
    )
    assert result.message.payload["silent"] is True


async def test_critical_keeps_its_sound(runtime: Runtime) -> None:
    runtime.service.config = make_config(
        defaults=Defaults(quiet_hours=QuietHours.parse("00:00-23:59"))
    )
    result = await runtime.service.enqueue(
        OutgoingMessage(alias="alerts", text="host down", level=Level.CRITICAL), key_id=None
    )
    assert result.message.payload["silent"] is False


async def test_explicit_silent_false_wins(runtime: Runtime) -> None:
    runtime.service.config = make_config(
        defaults=Defaults(quiet_hours=QuietHours.parse("00:00-23:59"))
    )
    result = await runtime.service.enqueue(
        OutgoingMessage(alias="alerts", text="wake up", level=Level.ERROR, silent=False),
        key_id=None,
    )
    assert result.message.payload["silent"] is False


async def test_without_quiet_hours_levels_decide(runtime: Runtime) -> None:
    result = await runtime.service.enqueue(
        OutgoingMessage(alias="alerts", text="deployed", level=Level.SUCCESS), key_id=None
    )
    assert result.message.payload["silent"] is True  # success is silent by default

    loud = await runtime.service.enqueue(
        OutgoingMessage(alias="alerts", text="disk full", level=Level.ERROR), key_id=None
    )
    assert loud.message.payload["silent"] is False
