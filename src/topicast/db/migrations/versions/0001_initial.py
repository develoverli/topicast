"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("key_id", sa.String(8), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("chat", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("telegram_message_ids", sa.JSON(), nullable=False),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("key_id", "idempotency_key", name="uq_messages_idempotency"),
    )
    op.create_index("ix_messages_due", "messages", ["status", "next_attempt_at"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_table(
        "dedupe_windows",
        sa.Column("fingerprint", sa.String(64), primary_key=True),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(32), nullable=False),
        sa.Column("preview", sa.Text(), nullable=False),
        sa.Column("suppressed", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_dedupe_windows_expires_at", "dedupe_windows", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_dedupe_windows_expires_at", table_name="dedupe_windows")
    op.drop_table("dedupe_windows")
    op.drop_index("ix_messages_created_at", table_name="messages")
    op.drop_index("ix_messages_due", table_name="messages")
    op.drop_table("messages")
    op.drop_table("api_keys")
