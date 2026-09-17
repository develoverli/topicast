"""Multipart upload handling: stream files to the spool directory."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from fastapi import Request
from starlette.datastructures import UploadFile

from topicast.config import Level, Settings
from topicast.delivery.formatting import Overflow, ParseMode
from topicast.delivery.telegram import MediaItem
from topicast.errors import ProblemError

CHUNK = 1 << 20
MAX_FILES = 10
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
PHOTO_MAX_BYTES = 10 * 1024 * 1024  # Telegram's limit for send_photo


@dataclass(slots=True)
class UploadedForm:
    to: str
    text: str | None
    parse_mode: ParseMode | None
    level: Level | None
    silent: bool | None
    disable_preview: bool
    dedupe_key: str | None
    overflow: Overflow
    media: list[MediaItem]
    digests: list[str]


def _bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _enum[T: StrEnum](factory: type[T], value: str | None, field: str) -> T | None:
    if not value:
        return None
    try:
        return factory(value)
    except ValueError as exc:
        raise ProblemError(422, "invalid_field", f"Invalid value for '{field}': {value}") from exc


def _parse_mode(value: str | None) -> ParseMode | None:
    if not value or value.lower() in {"plain", "none"}:
        return None
    if value.lower() == "html":
        return "HTML"
    if value.lower() == "markdownv2":
        return "MarkdownV2"
    raise ProblemError(422, "invalid_field", "parse_mode must be HTML, MarkdownV2 or plain.")


async def _store(upload: UploadFile, settings: Settings, kind_hint: str) -> tuple[MediaItem, str]:
    limit = settings.max_upload_mb * 1024 * 1024
    settings.spool_dir.mkdir(parents=True, exist_ok=True)
    path = settings.spool_dir / f"{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("wb") as handle:
            while chunk := await upload.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise ProblemError(
                        413,
                        "file_too_large",
                        f"'{upload.filename}' exceeds the {settings.max_upload_mb} MB limit.",
                    )
                digest.update(chunk)
                handle.write(chunk)
    except ProblemError:
        path.unlink(missing_ok=True)
        raise
    content_type = (upload.content_type or "").split(";")[0].lower()
    if kind_hint == "photo" or (
        kind_hint == "auto" and content_type in PHOTO_TYPES and size <= PHOTO_MAX_BYTES
    ):
        media_type = "photo"
    else:
        media_type = "document"
    item = MediaItem(
        type=media_type,  # type: ignore[arg-type]
        file=str(path),
        filename=upload.filename or path.name,
    )
    return item, digest.hexdigest()


async def parse_form(request: Request, settings: Settings) -> UploadedForm:
    async with request.form(max_files=MAX_FILES + 1, max_fields=32) as form:
        alias = str(form.get("to") or "").strip()
        if not alias:
            raise ProblemError(422, "missing_field", "Field 'to' is required.")
        kind_hint = str(form.get("as") or "auto").lower()
        if kind_hint not in {"auto", "photo", "document"}:
            raise ProblemError(422, "invalid_field", "'as' must be auto, photo or document.")

        uploads = [f for f in form.getlist("file") if isinstance(f, UploadFile)]
        if len(uploads) > MAX_FILES:
            raise ProblemError(422, "too_many_files", f"At most {MAX_FILES} files per request.")

        media: list[MediaItem] = []
        digests: list[str] = []
        try:
            for upload in uploads:
                item, digest = await _store(upload, settings, kind_hint)
                media.append(item)
                digests.append(digest)
        except ProblemError:
            for stored in media:
                if stored.file:
                    Path(stored.file).unlink(missing_ok=True)  # noqa: ASYNC240
            raise

        text_value = form.get("text")
        dedupe = form.get("dedupe_key")
        return UploadedForm(
            to=alias,
            text=str(text_value) if text_value is not None else None,
            parse_mode=_parse_mode(form.get("parse_mode")),  # type: ignore[arg-type]
            level=_enum(Level, form.get("level"), "level"),  # type: ignore[arg-type]
            silent=_bool(form.get("silent")),  # type: ignore[arg-type]
            disable_preview=bool(_bool(form.get("disable_preview"))),  # type: ignore[arg-type]
            dedupe_key=str(dedupe) if dedupe else None,
            overflow=_enum(Overflow, form.get("on_overflow"), "on_overflow")  # type: ignore[arg-type]
            or Overflow.SPLIT,
            media=media,
            digests=digests,
        )
