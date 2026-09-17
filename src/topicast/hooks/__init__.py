"""Inbound webhook adapters: turn third-party payloads into messages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from topicast.config import AppConfig, Level
from topicast.delivery.formatting import ParseMode


@dataclass(frozen=True, slots=True)
class HookMessage:
    text: str
    parse_mode: ParseMode | None = "HTML"
    level: Level | None = None
    dedupe_key: str | None = None


class HookRequest(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def query(self) -> Mapping[str, str]: ...


class HookError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


class HookAdapter(Protocol):
    name: str

    def verify(self, body: bytes, request: HookRequest, config: AppConfig) -> None:
        """Raise HookError if the request is not authentic. Default adapters accept all."""

    def render(self, payload: Any, request: HookRequest, config: AppConfig) -> HookMessage | None:
        """Return the message to send, or None to acknowledge and ignore the event."""
