"""API key generation, parsing and hashing.

Key format: ``tc_<id>_<secret>``

* ``tc_`` — fixed prefix, easy to spot with secret scanners.
* ``<id>`` — 8 hex chars, stored in clear and used for lookup.
* ``<secret>`` — 48 hex chars (192 bits).

Only ``HMAC-SHA256(TOPICAST_SECRET_KEY, full_key)`` is persisted.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from enum import StrEnum

KEY_PREFIX = "tc"
_KEY_PATTERN = re.compile(r"^tc_(?P<id>[0-9a-f]{8})_(?P<secret>[0-9a-f]{48})$")
ALL_ALIASES = "*"


class Scope(StrEnum):
    SEND = "send"
    EDIT = "edit"
    HOOKS = "hooks"


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    key_id: str
    token: str


def generate_key() -> GeneratedKey:
    key_id = secrets.token_hex(4)
    token = f"{KEY_PREFIX}_{key_id}_{secrets.token_hex(24)}"
    return GeneratedKey(key_id=key_id, token=token)


def parse_key_id(token: str) -> str | None:
    """Return the key id if `token` is well formed, else None."""
    match = _KEY_PATTERN.fullmatch(token.strip())
    return match.group("id") if match else None


def hash_key(token: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), token.strip().encode(), hashlib.sha256).hexdigest()


def verify_key(token: str, expected_hash: str, pepper: str) -> bool:
    return hmac.compare_digest(hash_key(token, pepper), expected_hash)


def redact(token: str) -> str:
    """Safe representation for logs: keeps only the public id."""
    key_id = parse_key_id(token)
    return f"{KEY_PREFIX}_{key_id}_***" if key_id else "***"
