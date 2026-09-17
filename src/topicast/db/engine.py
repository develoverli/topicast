"""Engine, sessions and migrations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_MIGRATIONS = Path(__file__).parent / "migrations"
_BUSY_TIMEOUT_MS = 5000


def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    cursor.close()


def sync_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def alembic_config(path: Path) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", sync_url(path))
    return cfg


def run_migrations(path: Path) -> None:
    """Upgrade the database at `path` to the latest schema (blocking)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(path), "head")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        event.listen(self.engine.sync_engine, "connect", _set_sqlite_pragmas)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()
