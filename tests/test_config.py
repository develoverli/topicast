from __future__ import annotations

from pathlib import Path

import pytest

from topicast.config import ConfigError, load_app_config

BASE = """\
bots:
  default:
    token: ${BOT_TOKEN}
chats:
  homelab:
    id: ${CHAT_ID}
aliases:
  alerts:
    chat: homelab
    topic: 5
"""

ENV = {"BOT_TOKEN": "123:ABC", "CHAT_ID": "-1001234567890"}


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_interpolates_environment(tmp_path: Path) -> None:
    config = load_app_config(write(tmp_path, BASE), ENV)
    assert config.bots["default"].token.get_secret_value() == "123:ABC"
    assert config.chats["homelab"].id == -1001234567890
    assert config.dedupe_window("alerts") == 60


def test_missing_variable_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        load_app_config(write(tmp_path, BASE), {"CHAT_ID": "-1"})


def test_default_value_syntax(tmp_path: Path) -> None:
    body = BASE.replace("${BOT_TOKEN}", "${BOT_TOKEN:-fallback}")
    config = load_app_config(write(tmp_path, body), {"CHAT_ID": "-1"})
    assert config.bots["default"].token.get_secret_value() == "fallback"


def test_unknown_chat_reference_is_rejected(tmp_path: Path) -> None:
    body = BASE.replace("chat: homelab", "chat: nowhere")
    with pytest.raises(ConfigError, match="unknown chat"):
        load_app_config(write(tmp_path, body), ENV)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_app_config(tmp_path / "absent.yaml", ENV)


def test_group_chats_default_to_twenty_per_minute(tmp_path: Path) -> None:
    config = load_app_config(write(tmp_path, BASE), ENV)
    assert config.chats["homelab"].effective_rate_per_minute == 20.0


GITHUB_SECRET = BASE + "hooks:\n  github:\n    secret: ${GH_SECRET:-}\n"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_github_secret_means_no_secret(tmp_path: Path, value: str) -> None:
    config = load_app_config(write(tmp_path, GITHUB_SECRET), {**ENV, "GH_SECRET": value})
    assert config.hooks.github.secret is None


def test_github_secret_is_kept_when_set(tmp_path: Path) -> None:
    config = load_app_config(write(tmp_path, GITHUB_SECRET), {**ENV, "GH_SECRET": "s3cret"})
    secret = config.hooks.github.secret
    assert secret is not None
    assert secret.get_secret_value() == "s3cret"
