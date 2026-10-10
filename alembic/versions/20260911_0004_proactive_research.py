"""Isolated experimental research sessions and decision audit.

Revision ID: 20260911_0004
Revises: 20260911_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20260911_0004"
down_revision = "20260911_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("proactive_sessions", sa.Column("token_hash", sa.String(64), primary_key=True),
                    sa.Column("audit_id", sa.String(36), nullable=False, unique=True),
                    sa.Column("expires_at", sa.Float(), nullable=False),
                    sa.Column("revision", sa.Integer(), nullable=False), sa.Column("state", sa.JSON(), nullable=False))
    op.create_index("ix_proactive_sessions_expires_at", "proactive_sessions", ["expires_at"])
    op.create_table("proactive_audits", sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("session_id", sa.String(36), nullable=False),
                    sa.Column("timestamp", sa.Float(), nullable=False), sa.Column("record", sa.JSON(), nullable=False))
    op.create_index("ix_proactive_audits_session_id", "proactive_audits", ["session_id"])
    op.create_index("ix_proactive_audits_timestamp", "proactive_audits", ["timestamp"])


def downgrade():
    op.drop_table("proactive_audits")
    op.drop_table("proactive_sessions")
