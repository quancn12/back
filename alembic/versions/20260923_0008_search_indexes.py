"""Own persistent Travel search indexes through Alembic."""

from alembic import op


revision = "20260923_0008"
down_revision = "20260923_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS travel_document_chunks_fts "
            "USING fts5("
            "id UNINDEXED, "
            "document_id UNINDEXED, "
            "country_code UNINDEXED, "
            "primary_destination_slug UNINDEXED, "
            "text, "
            "tokenize='unicode61 remove_diacritics 2'"
            ")"
        )
    elif dialect == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_search_fts "
            "ON document_chunks USING gin "
            "(to_tsvector('simple', search_text))"
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TABLE IF EXISTS travel_document_chunks_fts")
    elif dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_search_fts")
