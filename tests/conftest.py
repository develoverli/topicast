"""Shared fixtures: a runtime backed by a fake Telegram gateway."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from topicast.api.app import create_app
from topicast.config import AliasConfig, AppConfig, BotConfig, ChatConfig, Defaults, Settings
from topicast.delivery.formatting import ParseMode
from topicast.delivery.telegram import (
    BotIdentity,
    ChatInfo,
    DeliveryError,
    Keyboard,
    MediaItem,
    MemberInfo,
    Target,
)
from topicast.runtime import Runtime
from topicast.security import ALL_ALIASES, Scope

SECRET_KEY = "x" * 40


@dataclass
class SentMessage:
    target: Target
    text: str | None
    parse_mode: ParseMode | None
    silent: bool
    kind: str = "text"
    media: tuple[MediaItem, ...] = ()
    keyboard: Keyboard | None = None


@dataclass
class FakeGateway:
    """In-memory gateway. `errors` queues failures for the next calls."""

    sent: list[SentMessage] = field(default_factory=list)
    edits: list[tuple[int, str]] = field(default_factory=list)
    deleted: list[tuple[int, ...]] = field(default_factory=list)
    errors: list[DeliveryError] = field(default_factory=list)
    bot_error: DeliveryError | None = None
    chat_error: DeliveryError | None = None
    chat_info: ChatInfo = field(
        default_factory=lambda: ChatInfo(
            id=-1001234567890, type="supergroup", title="homelab", is_forum=True
        )
    )
    member_info: MemberInfo = field(
        default_factory=lambda: MemberInfo(status="administrator", can_post=True)
    )
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1000))

    def _maybe_fail(self) -> None:
        if self.errors:
            raise self.errors.pop(0)

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def get_me(self, bot: str) -> BotIdentity:
        if self.bot_error is not None:
            raise self.bot_error
        return BotIdentity(id=1, username=f"{bot}_bot")

    async def get_chat(self, bot: str, chat_id: int | str) -> ChatInfo:
        if self.chat_error is not None:
            raise self.chat_error
        return self.chat_info

    async def get_member(self, bot: str, chat_id: int | str, user_id: int) -> MemberInfo:
        if self.chat_error is not None:
            raise self.chat_error
        return self.member_info

    async def send_text(
        self,
        target: Target,
        text: str,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
        disable_preview: bool,
        keyboard: Keyboard | None = None,
    ) -> int:
        self._maybe_fail()
        self.sent.append(SentMessage(target, text, parse_mode, silent, keyboard=keyboard))
        return next(self._ids)

    async def send_media(
        self,
        target: Target,
        item: MediaItem,
        caption: str | None,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
        keyboard: Keyboard | None = None,
    ) -> int:
        self._maybe_fail()
        self.sent.append(
            SentMessage(
                target,
                caption,
                parse_mode,
                silent,
                kind=item.type,
                media=(item,),
                keyboard=keyboard,
            )
        )
        return next(self._ids)

    async def send_media_group(
        self,
        target: Target,
        items: Sequence[MediaItem],
        caption: str | None,
        *,
        parse_mode: ParseMode | None,
        silent: bool,
    ) -> list[int]:
        self._maybe_fail()
        self.sent.append(
            SentMessage(target, caption, parse_mode, silent, kind="media_group", media=tuple(items))
        )
        return [next(self._ids) for _ in items]

    async def edit_text(
        self,
        target: Target,
        message_id: int,
        text: str,
        *,
        parse_mode: ParseMode | None,
        caption: bool,
    ) -> None:
        self._maybe_fail()
        self.edits.append((message_id, text))

    async def delete(self, target: Target, message_ids: Sequence[int]) -> None:
        self._maybe_fail()
        self.deleted.append(tuple(message_ids))

    @property
    def texts(self) -> list[str | None]:
        return [m.text for m in self.sent]


def make_config(**overrides: Any) -> AppConfig:
    data: dict[str, Any] = {
        "bots": {"default": BotConfig(token="123:ABC")},
        # A high rate keeps the tests fast; real groups default to 20/minute.
        "chats": {"homelab": ChatConfig(id=-1001234567890, rate_per_minute=6000)},
        "aliases": {
            "alerts": AliasConfig(chat="homelab", topic=5),
            "deploys": AliasConfig(chat="homelab", topic=7, dedupe_window=0),
        },
        "defaults": Defaults(dedupe_window=60),
    }
    data.update(overrides)
    return AppConfig(**data)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        secret_key=SECRET_KEY,
        data_dir=tmp_path / "data",
        config_file=tmp_path / "config.yaml",
        wait_timeout=5.0,
        poll_interval=0.02,
        max_attempts=3,
    )


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
async def runtime(settings: Settings, gateway: FakeGateway) -> AsyncIterator[Runtime]:
    instance = Runtime.create(settings, config=make_config(), gateway=gateway)
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.fixture
async def api_key(runtime: Runtime) -> str:
    _, token = await runtime.keys.create(
        "tests", [Scope.SEND, Scope.EDIT, Scope.HOOKS], [ALL_ALIASES]
    )
    return token


@pytest.fixture
async def client(runtime: Runtime, api_key: str) -> AsyncIterator[AsyncClient]:
    app = create_app(runtime.settings, runtime)
    app.state.runtime = runtime  # the lifespan does not run under ASGITransport
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {api_key}"},
    ) as http:
        yield http


@pytest.fixture
def app_config() -> AppConfig:
    return make_config()
