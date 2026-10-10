"""Answer-quality feedback: one rating per assistant message.

Revision ID: 20260922_0006
Revises: 20260922_0005

Renumbered on merge: the auth migration was written on the same day and claimed
20260922_0005 too, which leaves Alembic with one id on two revisions and no
usable head.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260922_0006"
down_revision = "20260922_0005"
branch_labels = None
depends_on = None


# Adoption-safe for the same reason as 20260922_0005: before 20260923_0008 the
# app ran create_all() at startup, so a machine that started the app without
# migrating first already has this table while alembic_version still points
# at the previous revision. Create only what is missing; on an empty database
# the DDL is unchanged.
def _inspector():
    return sa.inspect(op.get_bind())


def _create_index(name, table, columns):
    if name not in {index["name"] for index in _inspector().get_indexes(table)}:
        op.create_index(name, table, columns)


def upgrade():
    if _inspector().has_table("message_feedback"):
        _create_indexes()
        return
    op.create_table(
        "message_feedback",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("unified_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.String(),
            sa.ForeignKey("unified_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("context_type", sa.String(16), nullable=False, server_default="TRAVEL"),
        sa.Column("locale", sa.String(12), nullable=False, server_default="vi"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        # One current opinion per answer, not a click log. Rating the same
        # message again replaces the row.
        sa.UniqueConstraint("message_id", name="uq_message_feedback_message"),
    )
    _create_indexes()


def _create_indexes():
    _create_index("ix_message_feedback_message_id", "message_feedback", ["message_id"])
    _create_index(
        "ix_message_feedback_conversation_id", "message_feedback", ["conversation_id"]
    )
    _create_index("ix_message_feedback_rating", "message_feedback", ["rating"])
    _create_index("ix_message_feedback_reason", "message_feedback", ["reason"])
    _create_index(
        "ix_message_feedback_context_type", "message_feedback", ["context_type"]
    )
    _create_index("ix_message_feedback_created_at", "message_feedback", ["created_at"])


def downgrade():
    op.drop_table("message_feedback")
