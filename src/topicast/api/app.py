"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from topicast import __version__, metrics
from topicast.api.routes import health, hooks, messages
from topicast.config import Settings, get_settings
from topicast.errors import install_error_handlers, problem_response
from topicast.runtime import Runtime

log = structlog.get_logger(__name__)

DESCRIPTION = """\
Send messages to Telegram forum topics from your services.

* Messages are queued, rate limited and retried; `?wait=true` waits for delivery.
* Aliases (`to`) hide chat ids and topic ids behind readable names.
* Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem document.

Authenticate with `Authorization: Bearer <key>` (or `X-API-Key`).
"""


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or (runtime.settings if runtime else get_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = runtime is None
        instance = runtime or Runtime.create(settings)
        app.state.runtime = instance
        if owned:
            await instance.start()
        try:
            yield
        finally:
            if owned:
                await instance.stop()

    app = FastAPI(
        title="topicast",
        version=__version__,
        summary="Self-hosted notification hub for Telegram forum topics.",
        description=DESCRIPTION,
        license_info={"name": "MIT", "identifier": "MIT"},
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def context_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Never leak a traceback to the caller.
            log.exception("unhandled_error", path=request.url.path)
            response = problem_response(500, "internal_error", "Unexpected server error.", request)
        duration = time.perf_counter() - started
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        metrics.HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        log.info(
            "request",
            method=request.method,
            path=request.url.path,  # query strings may carry hook tokens
            status=response.status_code,
            duration_ms=round(duration * 1000, 2),
        )
        structlog.contextvars.clear_contextvars()
        return response

    if settings.cors_origins:
        # Added last so it wraps everything, including error responses and preflights.
        # No credentials: keys travel in a header, never in cookies.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "X-API-Key", "Content-Type", "Idempotency-Key"],
            expose_headers=["X-Request-ID", "Retry-After"],
            max_age=600,
        )
        log.info("cors_enabled", origins=settings.cors_origins)

    install_error_handlers(app)
    app.include_router(messages.router)
    app.include_router(hooks.router)
    app.include_router(health.router)

    if settings.metrics_enabled:
        app.add_api_route(
            "/metrics",
            health.metrics_endpoint,
            methods=["GET"],
            include_in_schema=False,
        )

    @app.get("/", include_in_schema=False)
    def index() -> Response:
        if settings.docs_enabled:
            return RedirectResponse("/docs", status_code=302)
        return Response(status_code=204)

    return app
