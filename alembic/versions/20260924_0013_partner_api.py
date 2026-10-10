"""Partner API: partner keys and partner-owned conversations.

Adds ``partners`` (one row per partner website; only a SHA-256 of its API key)
and two nullable owner columns on ``unified_conversations`` for conversations
created through the partner API. Existing rows keep their account or guest
session owner and are unaffected.

Revision ID: 20260924_0013
Revises: 20260923_0012
"""

import sqlalchemy as sa
from alembic import op


revision = "20260924_0013"
down_revision = "20260923_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "partners" not in inspector.get_table_names():
        op.create_table(
            "partners",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("slug", sa.String(64), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("key_prefix", sa.String(16), nullable=False),
            sa.Column("key_hash", sa.String(64), nullable=False),
            sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_partners_slug", "partners", ["slug"], unique=True)
        op.create_index("ix_partners_key_prefix", "partners", ["key_prefix"], unique=True)
    existing = {column["name"] for column in inspector.get_columns("unified_conversations")}
    if "owner_partner_id" not in existing:
        op.add_column("unified_conversations", sa.Column("owner_partner_id", sa.String(36), nullable=True))
        op.create_index(
            "ix_unified_conversations_owner_partner_id", "unified_conversations", ["owner_partner_id"]
        )
    if "owner_partner_user" not in existing:
        op.add_column("unified_conversations", sa.Column("owner_partner_user", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_index("ix_unified_conversations_owner_partner_id", table_name="unified_conversations")
    with op.batch_alter_table("unified_conversations") as batch:
        batch.drop_column("owner_partner_user")
        batch.drop_column("owner_partner_id")
    op.drop_index("ix_partners_key_prefix", table_name="partners")
    op.drop_index("ix_partners_slug", table_name="partners")
    op.drop_table("partners")
