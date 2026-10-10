"""Create durable chat session tables."""
from alembic import op

from app.models.knowledge import ChatMessage, ChatSession


revision = "20260817_0002"
down_revision = "20260817_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ChatSession.__table__.create(bind=bind, checkfirst=True)
    ChatMessage.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    ChatMessage.__table__.drop(bind=bind, checkfirst=True)
    ChatSession.__table__.drop(bind=bind, checkfirst=True)
