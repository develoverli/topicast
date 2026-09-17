"""API key store."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from topicast.db import ApiKey, Database
from topicast.db.models import utcnow
from topicast.security import ALL_ALIASES, Scope, generate_key, hash_key, parse_key_id, verify_key

_TOUCH_INTERVAL = timedelta(minutes=1)


class KeyStoreError(Exception):
    """Invalid key operation (duplicate name, unknown key, …)."""


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller."""

    key_id: str
    name: str
    scopes: frozenset[Scope]
    aliases: frozenset[str]

    def can_use(self, alias: str) -> bool:
        return ALL_ALIASES in self.aliases or alias in self.aliases

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes


class KeyStore:
    def __init__(self, db: Database, pepper: str) -> None:
        self.db = db
        self._pepper = pepper

    async def create(
        self, name: str, scopes: Iterable[Scope], aliases: Iterable[str]
    ) -> tuple[ApiKey, str]:
        scope_list = sorted({s.value for s in scopes})
        alias_list = sorted(set(aliases))
        if not scope_list:
            msg = "at least one scope is required"
            raise KeyStoreError(msg)
        if not alias_list:
            msg = "at least one alias (or '*') is required"
            raise KeyStoreError(msg)
        async with self.db.session() as session:
            existing = await session.scalar(select(ApiKey).where(ApiKey.name == name))
            if existing is not None:
                msg = f"a key named '{name}' already exists"
                raise KeyStoreError(msg)
            generated = generate_key()
            key = ApiKey(
                id=generated.key_id,
                name=name,
                key_hash=hash_key(generated.token, self._pepper),
                scopes=scope_list,
                aliases=alias_list,
                created_at=utcnow(),
            )
            session.add(key)
            await session.commit()
        return key, generated.token

    async def list_keys(self, *, include_revoked: bool = False) -> list[ApiKey]:
        stmt = select(ApiKey).order_by(ApiKey.created_at)
        if not include_revoked:
            stmt = stmt.where(ApiKey.revoked_at.is_(None))
        async with self.db.session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def revoke(self, name: str) -> ApiKey:
        async with self.db.session() as session:
            key = await session.scalar(select(ApiKey).where(ApiKey.name == name))
            if key is None:
                msg = f"no key named '{name}'"
                raise KeyStoreError(msg)
            if key.revoked_at is None:
                key.revoked_at = utcnow()
                await session.commit()
        return key

    async def authenticate(self, token: str) -> Principal | None:
        key_id = parse_key_id(token)
        if key_id is None:
            return None
        async with self.db.session() as session:
            key = await session.get(ApiKey, key_id)
            if key is None or not key.active or not verify_key(token, key.key_hash, self._pepper):
                return None
            now = utcnow()
            if key.last_used_at is None or now - key.last_used_at > _TOUCH_INTERVAL:
                key.last_used_at = now
                await session.commit()
            return Principal(
                key_id=key.id,
                name=key.name,
                scopes=frozenset(Scope(s) for s in key.scopes),
                aliases=frozenset(key.aliases),
            )
