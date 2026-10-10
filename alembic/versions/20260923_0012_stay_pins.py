"""Per-stay PINs and reception's PIN-lockout unlock.

Adds ``stays.verification_pin_hash`` (a PIN issued for one stay, replacing the
room's standing PIN for that stay) and ``hotel_rooms.pin_lock_cleared_at``
(wrong PINs before this moment stop counting towards the lockout). Both are
nullable, so existing rooms and stays keep working exactly as before.

Revision ID: 20260923_0012
Revises: 20260923_0011
"""

import sqlalchemy as sa
from alembic import op


revision = "20260923_0012"
down_revision = "20260923_0011"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("stays", sa.Column("verification_pin_hash", sa.String(160), nullable=True)),
    ("hotel_rooms", sa.Column("pin_lock_cleared_at", sa.DateTime(), nullable=True)),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, column in _COLUMNS:
        if column.name not in {existing["name"] for existing in inspector.get_columns(table)}:
            op.add_column(table, column)


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column.name)
