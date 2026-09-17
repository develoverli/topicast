"""Request and response models for the v1 API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from topicast.config import Level
from topicast.db import Message, MessageStatus
from topicast.delivery.formatting import Overflow, ParseMode

MAX_TEXT = 40_000
MAX_SCHEDULE_DAYS = 365
Alias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$", examples=["alerts"])]


class MediaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["photo", "document"] = "document"
    url: str = Field(description="Public URL Telegram can download.")
    filename: str | None = None


class SendRequest(BaseModel):
    """A message to deliver. Files can also be uploaded as `multipart/form-data`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"to": "alerts", "text": "Deploy finished", "level": "success"},
                {
                    "to": "alerts",
                    "text": "<b>Disk</b> at 91%",
                    "parse_mode": "HTML",
                    "level": "warning",
                    "dedupe_key": "disk-usage",
                },
            ]
        },
    )

    to: Alias = Field(description="Alias configured in config.yaml.")
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    parse_mode: ParseMode | None = Field(
        default=None, description="`null` (plain), `HTML` or `MarkdownV2`."
    )
    level: Level | None = Field(
        default=None, description="Adds an emoji prefix and decides the default notification sound."
    )
    silent: bool | None = Field(
        default=None, description="Overrides the notification sound chosen by `level`."
    )
    disable_preview: bool = Field(default=False, description="Hide link previews.")
    dedupe_key: str | None = Field(
        default=None,
        max_length=255,
        description="Custom dedupe identity. Defaults to a hash of the content.",
    )
    on_overflow: Overflow = Field(
        default=Overflow.SPLIT, description="What to do with text over Telegram's limit."
    )
    media: list[MediaInput] = Field(default_factory=list, max_length=10)
    send_at: datetime | None = Field(
        default=None,
        description=(
            "Deliver at this time instead of now (ISO 8601, with a timezone). "
            "Up to 365 days ahead. Cancel with DELETE before it is sent."
        ),
        examples=["2026-09-18T09:00:00Z"],
    )

    @field_validator("send_at")
    @classmethod
    def _check_send_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            msg = "send_at needs a timezone, e.g. 2026-09-18T09:00:00Z"
            raise ValueError(msg)
        now = datetime.now(UTC)
        if value <= now - timedelta(minutes=1):
            msg = "send_at is in the past"
            raise ValueError(msg)
        if value > now + timedelta(days=MAX_SCHEDULE_DAYS):
            msg = f"send_at is more than {MAX_SCHEDULE_DAYS} days away"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _needs_content(self) -> Self:
        if not self.text and not self.media:
            msg = "provide `text`, `media`, or upload a file"
            raise ValueError(msg)
        return self


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_TEXT)
    parse_mode: ParseMode | None = None
    level: Level | None = None
    on_overflow: Literal[Overflow.REJECT, Overflow.TRUNCATE] = Overflow.REJECT


class MessageResponse(BaseModel):
    id: str
    status: str
    to: str
    kind: str
    attempts: int
    telegram_message_ids: list[int]
    created_at: datetime
    scheduled_for: datetime | None = Field(
        default=None, description="Set when the message waits for a `send_at` time."
    )
    delivered_at: datetime | None = None
    fallback_reason: str | None = Field(
        default=None, description="Set when Telegram rejected the markup and plain text was sent."
    )
    last_error: str | None = None
    deduplicated: bool = False

    @classmethod
    def from_model(cls, message: Message, *, deduplicated: bool = False) -> MessageResponse:
        return cls(
            id=message.id,
            status=message.status,
            to=message.alias,
            kind=message.kind,
            attempts=message.attempts,
            telegram_message_ids=list(message.telegram_message_ids),
            created_at=message.created_at,
            scheduled_for=(
                message.next_attempt_at
                if message.status == MessageStatus.QUEUED
                and message.attempts == 0
                and message.next_attempt_at > message.created_at
                else None
            ),
            delivered_at=message.delivered_at,
            fallback_reason=message.fallback_reason,
            last_error=message.last_error,
            deduplicated=deduplicated,
        )


class Problem(BaseModel):
    """RFC 9457 problem details."""

    type: str = "about:blank"
    title: str
    status: int
    code: str
    detail: str
    instance: str | None = None
    request_id: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    version: str
    database: bool
    worker: bool
    bots: dict[str, str]
