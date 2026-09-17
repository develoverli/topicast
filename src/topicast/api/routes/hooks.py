"""`/v1/hooks/{source}/{alias}` — inbound webhooks.

Most senders cannot set custom headers, so the key travels in the `token` query
parameter. Such keys need the `hooks` scope and can do nothing else.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Query, Request, Response, status

from topicast.api.deps import RuntimeDep, authenticate, bearer_token, require_alias, require_scope
from topicast.api.schemas import MessageResponse, Problem
from topicast.delivery.service import OutgoingMessage, ServiceError
from topicast.errors import ProblemError
from topicast.hooks import HookError
from topicast.hooks.registry import ADAPTERS
from topicast.security import Scope

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/hooks", tags=["hooks"])

MAX_BODY_BYTES = 1 << 20


class _RequestView:
    """Read-only view of the request handed to hook adapters."""

    def __init__(self, request: Request) -> None:
        self.headers = {k.lower(): v for k, v in request.headers.items()}
        self.query = dict(request.query_params)


@router.post(
    "/{source}/{alias}",
    summary="Receive a webhook",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageResponse,
    responses={
        204: {"description": "Event acknowledged and ignored"},
        401: {"model": Problem, "description": "Missing, invalid or unsigned request"},
        403: {"model": Problem, "description": "Key lacks the `hooks` scope or the alias"},
        404: {"model": Problem, "description": "Unknown source, alias or template"},
        413: {"model": Problem, "description": "Payload too large"},
        422: {"model": Problem, "description": "Payload the adapter cannot read"},
    },
)
async def receive_hook(
    source: str,
    alias: str,
    request: Request,
    runtime: RuntimeDep,
    token: Annotated[str | None, Query(description="API key with the `hooks` scope.")] = None,
) -> Response:
    adapter = ADAPTERS.get(source)
    if adapter is None:
        raise ProblemError(
            404,
            "unknown_source",
            f"Unknown webhook source '{source}'. Available: {', '.join(sorted(ADAPTERS))}.",
        )
    principal = await authenticate(request, token or bearer_token(request))
    require_scope(principal, Scope.HOOKS)
    require_alias(principal, alias)

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise ProblemError(413, "payload_too_large", "Webhook payload exceeds 1 MB.")

    view = _RequestView(request)
    try:
        adapter.verify(body, view, runtime.config)
        payload: Any = json.loads(body or b"{}")
    except HookError as exc:
        raise ProblemError(exc.status, exc.code, exc.detail) from exc
    except json.JSONDecodeError as exc:
        raise ProblemError(422, "invalid_json", f"Body is not valid JSON: {exc}") from exc

    try:
        message = adapter.render(payload, view, runtime.config)
    except HookError as exc:
        raise ProblemError(exc.status, exc.code, exc.detail) from exc
    if message is None:
        log.info("hook_ignored", source=source, alias=alias)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    out = OutgoingMessage(
        alias=alias,
        text=message.text,
        parse_mode=message.parse_mode,
        level=message.level,
        dedupe_key=message.dedupe_key,
        source=f"hook:{source}",
    )
    try:
        result = await runtime.service.enqueue(out, key_id=principal.key_id)
    except ServiceError as exc:
        raise ProblemError(exc.status, exc.code, exc.detail) from exc
    code = status.HTTP_200_OK if result.outcome != "queued" else status.HTTP_202_ACCEPTED
    return Response(
        content=MessageResponse.from_model(
            result.message, deduplicated=result.outcome == "deduplicated"
        ).model_dump_json(),
        status_code=code,
        media_type="application/json",
    )
