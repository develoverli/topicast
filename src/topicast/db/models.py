"""Database schema (SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores naive UTC in SQLite, always returns aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            msg = "naive datetimes are not allowed"
            raise ValueError(msg)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSON,
        list[str]: JSON,
        list[int]: JSON,
    }


class MessageStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"
    DELETED = "deleted"


FINAL_STATUSES = frozenset({MessageStatus.DELIVERED, MessageStatus.FAILED, MessageStatus.DELETED})


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]]
    aliases: Mapped[list[str]]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("key_id", "idempotency_key", name="uq_messages_idempotency"),
        Index("ix_messages_due", "status", "next_attempt_at"),
        Index("ix_messages_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_id: Mapped[str | None] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(32), default="api")
    alias: Mapped[str] = mapped_column(String(64))
    chat: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(16), default=MessageStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    telegram_message_ids: Mapped[list[int]] = mapped_column(default=list)
    fallback_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DedupeWindow(Base):
    __tablename__ = "dedupe_windows"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    alias: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str] = mapped_column(String(32))
    preview: Mapped[str] = mapped_column(Text, default="")
    suppressed: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
