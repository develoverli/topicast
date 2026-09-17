"""`/v1/messages` — send, inspect, edit and delete."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from topicast.api.deps import PrincipalDep, RuntimeDep, require_alias, require_scope
from topicast.api.schemas import EditRequest, MessageResponse, Problem, SendRequest
from topicast.api.uploads import parse_form
from topicast.db import MessageStatus
from topicast.delivery.service import EnqueueResult, OutgoingMessage, ServiceError
from topicast.delivery.telegram import MediaItem
from topicast.errors import ProblemError
from topicast.keys import Principal
from topicast.runtime import Runtime
from topicast.security import Scope

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/messages", tags=["messages"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": Problem, "description": "Missing or invalid API key"},
    403: {"model": Problem, "description": "Key lacks the scope or the alias"},
    404: {"model": Problem, "description": "Unknown alias or message"},
    422: {"model": Problem, "description": "Invalid request"},
    429: {"model": Problem, "description": "Telegram flood limit hit"},
}


def _problem(exc: ServiceError) -> ProblemError:
    headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after is not None else None
    return ProblemError(exc.status, exc.code, exc.detail, headers=headers)


async def _json_body(request: Request) -> SendRequest:
    raw = await request.body()
    try:
        data = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise ProblemError(422, "invalid_json", f"Body is not valid JSON: {exc}") from exc
    try:
        return SendRequest.model_validate(data)
    except ValidationError as exc:
        raise ProblemError(
            422,
            "validation_error",
            "Request validation failed.",
            errors=[
                {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type")}
                for e in exc.errors()
            ],
        ) from exc


def _cleanup(media: list[MediaItem]) -> None:
    for item in media:
        if item.file:
            Path(item.file).unlink(missing_ok=True)


async def _respond(
    runtime: Runtime, result: EnqueueResult, *, wait: bool, request: Request
) -> Response:
    message = result.message
    if result.outcome == "deduplicated":
        return JSONResponse(
            MessageResponse.from_model(message, deduplicated=True).model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )
    scheduled = message.next_attempt_at > message.created_at
    if wait and scheduled:
        # Waiting for a message scheduled for later would just burn the timeout.
        return JSONResponse(
            MessageResponse.from_model(message).model_dump(mode="json"),
            status_code=status.HTTP_202_ACCEPTED,
        )
    if wait:
        final = await runtime.worker.wait_for(message.id, runtime.settings.wait_timeout)
        if final is None:
            return JSONResponse(
                MessageResponse.from_model(message).model_dump(mode="json"),
                status_code=status.HTTP_202_ACCEPTED,
            )
        if final.status == MessageStatus.FAILED:
            raise ProblemError(
                502,
                "delivery_failed",
                final.last_error or "Telegram rejected the message.",
                message_id=final.id,
            )
        return JSONResponse(
            MessageResponse.from_model(final).model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )
    code = status.HTTP_200_OK if result.outcome == "existing" else status.HTTP_202_ACCEPTED
    return JSONResponse(
        MessageResponse.from_model(message).model_dump(mode="json"), status_code=code
    )


def _check(principal: Principal, alias: str, scope: Scope = Scope.SEND) -> None:
    require_scope(principal, scope)
    require_alias(principal, alias)


@router.post(
    "",
    summary="Send a message",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageResponse,
    responses={
        200: {"model": MessageResponse, "description": "Delivered, deduplicated or replayed"},
        202: {"model": MessageResponse, "description": "Queued for delivery"},
        413: {"model": Problem, "description": "Uploaded file too large"},
        502: {"model": Problem, "description": "Delivery failed (only with `wait=true`)"},
        **_ERRORS,
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/SendRequest"}},
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string"},
                            "text": {"type": "string"},
                            "file": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            },
                            "as": {"type": "string", "enum": ["auto", "photo", "document"]},
                            "parse_mode": {"type": "string"},
                            "level": {"type": "string"},
                            "silent": {"type": "boolean"},
                            "dedupe_key": {"type": "string"},
                            "on_overflow": {"type": "string"},
                        },
                        "required": ["to"],
                    }
                },
            },
            "required": True,
        }
    },
)
async def send_message(
    request: Request,
    runtime: RuntimeDep,
    principal: PrincipalDep,
    wait: Annotated[bool, Query(description="Wait for delivery before answering.")] = False,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    media: list[MediaItem] = []
    if content_type == "multipart/form-data":
        form = await parse_form(request, runtime.settings)
        media = form.media
        _check(principal, form.to)
        out = OutgoingMessage(
            alias=form.to,
            text=form.text,
            parse_mode=form.parse_mode,
            level=form.level,
            silent=form.silent,
            disable_preview=form.disable_preview,
            dedupe_key=form.dedupe_key,
            overflow=form.overflow,
            media=form.media,
            media_digests=form.digests,
        )
    else:
        payload = await _json_body(request)
        _check(principal, payload.to)
        out = OutgoingMessage(
            alias=payload.to,
            text=payload.text,
            parse_mode=payload.parse_mode,
            level=payload.level,
            silent=payload.silent,
            disable_preview=payload.disable_preview,
            dedupe_key=payload.dedupe_key,
            overflow=payload.on_overflow,
            media=[MediaItem(type=m.type, url=m.url, filename=m.filename) for m in payload.media],
            send_at=payload.send_at,
        )

    try:
        result = await runtime.service.enqueue(
            out, key_id=principal.key_id, idempotency_key=idempotency_key
        )
    except ServiceError as exc:
        _cleanup(media)
        raise _problem(exc) from exc
    if result.outcome != "queued":
        _cleanup(media)
    return await _respond(runtime, result, wait=wait, request=request)


@router.get(
    "/{message_id}",
    summary="Message status",
    response_model=MessageResponse,
    responses=_ERRORS,
)
async def get_message(
    message_id: str, runtime: RuntimeDep, principal: PrincipalDep
) -> MessageResponse:
    require_scope(principal, Scope.SEND)
    message = await runtime.service.get(message_id)
    if message is None or not principal.can_use(message.alias):
        raise ProblemError(404, "message_not_found", "Message not found.")
    return MessageResponse.from_model(message)


@router.patch(
    "/{message_id}",
    summary="Edit a delivered message",
    response_model=MessageResponse,
    responses={409: {"model": Problem, "description": "Message is not delivered"}, **_ERRORS},
)
async def edit_message(
    message_id: str, body: EditRequest, runtime: RuntimeDep, principal: PrincipalDep
) -> MessageResponse:
    require_scope(principal, Scope.EDIT)
    message = await runtime.service.get(message_id)
    if message is None or not principal.can_use(message.alias):
        raise ProblemError(404, "message_not_found", "Message not found.")
    try:
        updated = await runtime.service.edit(
            message_id,
            text=body.text,
            parse_mode=body.parse_mode,
            level=body.level,
            overflow=body.on_overflow,
        )
    except ServiceError as exc:
        raise _problem(exc) from exc
    return MessageResponse.from_model(updated)


@router.delete(
    "/{message_id}",
    summary="Delete a message (or cancel a queued one)",
    response_model=MessageResponse,
    responses={409: {"model": Problem, "description": "Message is being delivered"}, **_ERRORS},
)
async def delete_message(
    message_id: str, runtime: RuntimeDep, principal: PrincipalDep
) -> MessageResponse:
    require_scope(principal, Scope.EDIT)
    message = await runtime.service.get(message_id)
    if message is None or not principal.can_use(message.alias):
        raise ProblemError(404, "message_not_found", "Message not found.")
    try:
        deleted = await runtime.service.delete(message_id)
    except ServiceError as exc:
        raise _problem(exc) from exc
    return MessageResponse.from_model(deleted)
