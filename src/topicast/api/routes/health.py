"""Liveness, readiness and metrics."""

from __future__ import annotations

from fastapi import APIRouter, Response

from topicast import __version__
from topicast.api.deps import RuntimeDep
from topicast.api.schemas import HealthResponse, ReadyResponse
from topicast.metrics import REGISTRY

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", summary="Readiness probe", response_model=ReadyResponse)
async def readyz(runtime: RuntimeDep, response: Response) -> ReadyResponse:
    try:
        database = await runtime.db.ping()
    except Exception:
        database = False
    bots = await runtime.check_bots(force=False)
    ready = database and runtime.worker.running and all(v == "ok" for v in bots.values())
    if not ready:
        response.status_code = 503
    return ReadyResponse(
        status="ready" if ready else "degraded",
        version=__version__,
        database=database,
        worker=runtime.worker.running,
        bots=bots,
    )


def metrics_endpoint() -> Response:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
