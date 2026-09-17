from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.conftest import SECRET_KEY
from topicast.cli import app
from topicast.config import get_settings

runner = CliRunner()

CONFIG = """\
bots:
  default:
    token: ${TELEGRAM_BOT_TOKEN}
chats:
  homelab:
    id: -1001234567890
aliases:
  alerts:
    chat: homelab
    topic: 5
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("TOPICAST_SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("TOPICAST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOPICAST_CONFIG_FILE", str(config))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0


def test_init_writes_a_config_and_a_secret(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert "bots:" in target.read_text(encoding="utf-8")
    assert "TOPICAST_SECRET_KEY=" in result.stdout

    again = runner.invoke(app, ["init", "--config", str(target)])
    assert again.exit_code == 1  # refuses to overwrite


def test_check_config_lists_aliases(workspace: Path) -> None:
    result = runner.invoke(app, ["check-config"])
    assert result.exit_code == 0, result.stdout
    assert "alerts" in result.stdout
    assert "topic 5" in result.stdout


def test_check_config_reports_a_broken_file(workspace: Path) -> None:
    (workspace / "config.yaml").write_text("bots: [oops]", encoding="utf-8")
    result = runner.invoke(app, ["check-config"])
    assert result.exit_code == 1
    assert "error" in result.stderr.lower()  # errors go to stderr


def test_key_lifecycle(workspace: Path) -> None:
    created = runner.invoke(app, ["keys", "create", "ci", "--scope", "send", "--alias", "alerts"])
    assert created.exit_code == 0, created.stdout
    assert "tc_" in created.stdout

    duplicate = runner.invoke(app, ["keys", "create", "ci"])
    assert duplicate.exit_code == 1

    listed = runner.invoke(app, ["keys", "list"])
    assert "ci" in listed.stdout
    assert "active" in listed.stdout

    revoked = runner.invoke(app, ["keys", "revoke", "ci"])
    assert revoked.exit_code == 0
    assert "no keys yet" in runner.invoke(app, ["keys", "list"]).stdout


def test_revoking_an_unknown_key_fails(workspace: Path) -> None:
    assert runner.invoke(app, ["keys", "revoke", "ghost"]).exit_code == 1


def test_send_queues_a_message(workspace: Path) -> None:
    result = runner.invoke(app, ["send", "alerts", "from the cli", "--level", "info"])
    assert result.exit_code == 0, result.stdout
    assert "queued" in result.stdout

    listed = runner.invoke(app, ["messages", "list"])
    assert "alerts" in listed.stdout
    assert "queued" in listed.stdout


def test_send_to_an_unknown_alias_fails(workspace: Path) -> None:
    result = runner.invoke(app, ["send", "nowhere", "hi"])
    assert result.exit_code == 1
    assert "not configured" in result.stderr
