"""Authentication and shared dependencies."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Depends, Request

from topicast.errors import ProblemError
from topicast.keys import Principal
from topicast.runtime import Runtime
from topicast.security import Scope

log = structlog.get_logger(__name__)


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-api-key")


async def authenticate(request: Request, token: str | None) -> Principal:
    if not token:
        raise ProblemError(
            401,
            "missing_api_key",
            "Send your key in `Authorization: Bearer <key>` or `X-API-Key`.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    runtime = get_runtime(request)
    principal = await runtime.keys.authenticate(token)
    if principal is None:
        log.warning(
            "auth_failed",
            path=request.url.path,
            client=request.client.host if request.client else None,
        )
        raise ProblemError(401, "invalid_api_key", "The API key is invalid or revoked.")
    structlog.contextvars.bind_contextvars(key=principal.name)
    return principal


async def current_principal(request: Request) -> Principal:
    return await authenticate(request, bearer_token(request))


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require_scope(principal: Principal, scope: Scope) -> None:
    if not principal.has(scope):
        raise ProblemError(
            403, "missing_scope", f"This key does not have the '{scope.value}' scope."
        )


def require_alias(principal: Principal, alias: str) -> None:
    if not principal.can_use(alias):
        raise ProblemError(403, "alias_not_allowed", f"This key cannot send to '{alias}'.")
