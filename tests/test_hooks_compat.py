"""Slack and Discord compatibility endpoints, with payloads real tools send."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from httpx import AsyncClient

from tests.conftest import FakeGateway
from topicast.config import AppConfig, Level
from topicast.hooks import HookError
from topicast.hooks.discord import DiscordAdapter
from topicast.hooks.markup import discord_to_html, slack_to_html
from topicast.hooks.slack import SlackAdapter
from topicast.runtime import Runtime
from topicast.security import ALL_ALIASES, Scope


class _Request:
    headers: ClassVar[dict[str, str]] = {}
    query: ClassVar[dict[str, str]] = {}


def render_slack(payload: Any, config: AppConfig) -> tuple[str, Level | None]:
    message = SlackAdapter().render(payload, _Request(), config)
    return message.text, message.level


def render_discord(payload: Any, config: AppConfig) -> tuple[str, Level | None]:
    message = DiscordAdapter().render(payload, _Request(), config)
    return message.text, message.level


# --------------------------------------------------------------------------- markup


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("*bold* and _italic_", "<b>bold</b> and <i>italic</i>"),
        ("`code`", "<code>code</code>"),
        ("<https://ex.com|the site>", '<a href="https://ex.com">the site</a>'),
        ("<https://ex.com>", '<a href="https://ex.com">https://ex.com</a>'),
        ("5 < 7 & rising", "5 &lt; 7 &amp; rising"),
    ],
)
def test_slack_markup(raw: str, expected: str) -> None:
    assert slack_to_html(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**bold** and *italic*", "<b>bold</b> and <i>italic</i>"),
        ("~~gone~~", "<s>gone</s>"),
        ("[docs](https://ex.com/a)", '<a href="https://ex.com/a">docs</a>'),
        ("a & b", "a &amp; b"),
    ],
)
def test_discord_markup(raw: str, expected: str) -> None:
    assert discord_to_html(raw) == expected


def test_labels_cannot_inject_markup() -> None:
    rendered = slack_to_html("<https://ex.com|<script>alert(1)</script>>")
    assert "<script>" not in rendered
    assert "&lt;script" in rendered
    # Only the anchor we build survives as real markup.
    assert rendered.count("<a href=") == 1


def test_code_blocks_survive_escaping() -> None:
    assert discord_to_html("```\n<b>raw</b>\n```") == "<pre>&lt;b&gt;raw&lt;/b&gt;</pre>"


# ---------------------------------------------------------------------------- slack


def test_slack_plain_text(app_config: AppConfig) -> None:
    text, level = render_slack({"text": "Backup finished"}, app_config)
    assert text == "Backup finished"
    assert level is None


def test_slack_attachment_sets_the_level(app_config: AppConfig) -> None:
    payload = {
        "attachments": [
            {
                "color": "danger",
                "title": "Disk full",
                "title_link": "https://grafana.local/d/abc",
                "text": "/var at 98%",
                "fields": [{"title": "Host", "value": "nas"}],
                "footer": "netdata",
            }
        ]
    }
    text, level = render_slack(payload, app_config)
    assert level is Level.ERROR
    assert '<a href="https://grafana.local/d/abc">Disk full</a>' in text
    assert "/var at 98%" in text
    assert "<b>Host:</b> nas" in text
    assert "<i>netdata</i>" in text


def test_slack_blocks(app_config: AppConfig) -> None:
    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Deploy done"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*web* is live"}},
        ]
    }
    text, _ = render_slack(payload, app_config)
    assert "Deploy done" in text
    assert "<b>web</b> is live" in text


def test_slack_good_color_is_success(app_config: AppConfig) -> None:
    _, level = render_slack({"attachments": [{"color": "good", "text": "ok"}]}, app_config)
    assert level is Level.SUCCESS


def test_slack_empty_payload_is_rejected(app_config: AppConfig) -> None:
    with pytest.raises(HookError) as exc:
        render_slack({}, app_config)
    assert exc.value.code == "empty_message"


# -------------------------------------------------------------------------- discord


def test_discord_content_only(app_config: AppConfig) -> None:
    text, level = render_discord({"content": "container restarted"}, app_config)
    assert text == "container restarted"
    assert level is None


def test_discord_embed_like_watchtower(app_config: AppConfig) -> None:
    payload = {
        "username": "Watchtower",
        "embeds": [
            {
                "title": "Updated 2 containers",
                "url": "https://portainer.local",
                "description": "nginx, redis",
                "color": 3066993,  # green
                "fields": [{"name": "Host", "value": "vps-1", "inline": True}],
                "footer": {"text": "watchtower 1.7"},
            }
        ],
    }
    text, level = render_discord(payload, app_config)
    assert level is Level.SUCCESS
    assert '<a href="https://portainer.local">Updated 2 containers</a>' in text
    assert "nginx, redis" in text
    assert "<b>Host:</b> vps-1" in text
    assert "<i>watchtower 1.7</i>" in text


@pytest.mark.parametrize(
    ("color", "expected"),
    [(15158332, Level.ERROR), (16776960, Level.WARNING), (3066993, Level.SUCCESS), ("x", None)],
)
def test_discord_colors_map_to_levels(
    color: object, expected: Level | None, app_config: AppConfig
) -> None:
    _, level = render_discord({"content": "hi", "embeds": [{"color": color}]}, app_config)
    assert level is expected


def test_discord_username_without_content(app_config: AppConfig) -> None:
    text, _ = render_discord({"username": "Sonarr", "embeds": [{"title": "Grabbed"}]}, app_config)
    assert text.startswith("<b>Sonarr</b>")


def test_discord_empty_payload_is_rejected(app_config: AppConfig) -> None:
    with pytest.raises(HookError) as exc:
        render_discord({"username": "nobody"}, app_config)
    assert exc.value.code == "empty_message"


# ------------------------------------------------------------------------ endpoints


async def test_slack_endpoint_delivers(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    _, token = await runtime.keys.create("slack-hook", [Scope.HOOKS], [ALL_ALIASES])
    response = await client.post(
        f"/v1/hooks/slack/alerts?token={token}",
        json={"text": "deploy *finished*", "attachments": [{"color": "good"}]},
    )
    assert response.status_code == 202, response.text
    assert await runtime.worker.wait_for(response.json()["id"], timeout=5)
    sent = gateway.sent[0]
    assert sent.text == "✅ deploy <b>finished</b>"
    assert sent.parse_mode == "HTML"


async def test_discord_endpoint_delivers(
    client: AsyncClient, gateway: FakeGateway, runtime: Runtime
) -> None:
    _, token = await runtime.keys.create("discord-hook", [Scope.HOOKS], [ALL_ALIASES])
    response = await client.post(
        f"/v1/hooks/discord/alerts?token={token}",
        json={"content": "**redis** down", "embeds": [{"color": 15158332}]},
    )
    assert response.status_code == 202, response.text
    assert await runtime.worker.wait_for(response.json()["id"], timeout=5)
    assert gateway.sent[0].text == "❌ <b>redis</b> down"
