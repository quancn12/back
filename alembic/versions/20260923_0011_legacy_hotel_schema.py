"""Own the legacy Hotel schema in Alembic instead of at startup.

Until now the legacy Hotel flag ran two DDL helpers in ``app.main`` on every
launch: add ``hotel_documents.content_hash`` (and its index) to SQLite files
created before that column existed, and create the
``hotel_document_chunks_fts`` virtual table. Runtime DDL is gone everywhere else
(roadmap B6), so both move here.

Idempotent: a database built from the 0001 baseline already has the column and
index, and a database that ran the old helpers already has the FTS table.

Revision ID: 20260923_0011
Revises: 20260923_0010
"""

import sqlalchemy as sa
from alembic import op


revision = "20260923_0011"
down_revision = "20260923_0010"
branch_labels = None
depends_on = None

CONTENT_HASH_INDEX = "ix_hotel_documents_content_hash"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "hotel_documents" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("hotel_documents")}
        if "content_hash" not in columns:
            op.add_column("hotel_documents", sa.Column("content_hash", sa.String(), nullable=True))
        indexes = {index["name"] for index in sa.inspect(bind).get_indexes("hotel_documents")}
        if CONTENT_HASH_INDEX not in indexes:
            op.create_index(CONTENT_HASH_INDEX, "hotel_documents", ["content_hash"])
    # The legacy Hotel keyword search only exists on SQLite (FTS5).
    if bind.dialect.name == "sqlite":
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS hotel_document_chunks_fts USING fts5("
            "id UNINDEXED, hotel_id UNINDEXED, document_id UNINDEXED, text)"
        )


def downgrade() -> None:
    # The column and its index belong to the 0001 baseline model, so only the
    # FTS table is removed. Re-ingesting Hotel documents rebuilds its rows.
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TABLE IF EXISTS hotel_document_chunks_fts")
