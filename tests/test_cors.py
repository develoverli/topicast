from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import SECRET_KEY, FakeGateway, make_config
from topicast.api.app import create_app
from topicast.config import Settings
from topicast.runtime import Runtime

PANEL = "https://panel.example.com"


def settings_with(origins: str, tmp_path: Path) -> Settings:
    return Settings(
        secret_key=SECRET_KEY,
        data_dir=tmp_path / "data",
        config_file=tmp_path / "config.yaml",
        cors_origins=origins,  # parsed from a comma separated string
    )


async def client_for(settings: Settings, gateway: FakeGateway) -> AsyncIterator[AsyncClient]:
    runtime = Runtime.create(settings, config=make_config(), gateway=gateway)
    await runtime.start()
    app = create_app(settings, runtime)
    app.state.runtime = runtime
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            yield http
    finally:
        await runtime.stop()


def test_origins_are_parsed_from_a_comma_separated_string(tmp_path: Path) -> None:
    settings = settings_with(f"{PANEL}, http://localhost:5173 ", tmp_path)
    assert settings.cors_origins == [PANEL, "http://localhost:5173"]


def test_trailing_slashes_are_dropped(tmp_path: Path) -> None:
    assert settings_with(f"{PANEL}/", tmp_path).cors_origins == [PANEL]


def test_wildcard_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"does not accept '\*'"):
        settings_with("*", tmp_path)


def test_garbage_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid origin"):
        settings_with("panel.example.com", tmp_path)


async def test_no_cors_headers_by_default(tmp_path: Path, gateway: FakeGateway) -> None:
    async for client in client_for(settings_with("", tmp_path), gateway):
        response = await client.get("/healthz", headers={"Origin": PANEL})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers


async def test_configured_origin_is_allowed(tmp_path: Path, gateway: FakeGateway) -> None:
    async for client in client_for(settings_with(PANEL, tmp_path), gateway):
        response = await client.get("/healthz", headers={"Origin": PANEL})
        assert response.headers["access-control-allow-origin"] == PANEL
        # Keys are sent in a header, so cookies must never be allowed.
        assert "access-control-allow-credentials" not in response.headers


async def test_other_origins_stay_blocked(tmp_path: Path, gateway: FakeGateway) -> None:
    async for client in client_for(settings_with(PANEL, tmp_path), gateway):
        response = await client.get("/healthz", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in response.headers


async def test_preflight_allows_the_api_key_header(tmp_path: Path, gateway: FakeGateway) -> None:
    async for client in client_for(settings_with(PANEL, tmp_path), gateway):
        response = await client.options(
            "/v1/messages",
            headers={
                "Origin": PANEL,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == PANEL
        assert "POST" in response.headers["access-control-allow-methods"]


async def test_preflight_rejects_delete(tmp_path: Path, gateway: FakeGateway) -> None:
    async for client in client_for(settings_with(PANEL, tmp_path), gateway):
        response = await client.options(
            "/v1/messages/abc",
            headers={"Origin": PANEL, "Access-Control-Request-Method": "DELETE"},
        )
        # Starlette answers the preflight with 400, and the browser blocks the call.
        assert response.status_code == 400
        assert "DELETE" not in response.headers["access-control-allow-methods"]
