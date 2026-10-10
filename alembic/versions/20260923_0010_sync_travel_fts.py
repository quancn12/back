"""Populate and maintain the SQLite Travel FTS index.

Revision ID: 20260923_0010
Revises: 20260923_0009
"""

import re
import unicodedata

import sqlalchemy as sa
from alembic import op


revision = "20260923_0010"
down_revision = "20260923_0009"
branch_labels = None
depends_on = None


def _normalize_search_text(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, text FROM document_chunks")).all()
    for chunk_id, text in rows:
        bind.execute(
            sa.text("UPDATE document_chunks SET search_text = :search_text WHERE id = :id"),
            {"id": chunk_id, "search_text": _normalize_search_text(text or "")},
        )

    if bind.dialect.name != "sqlite":
        return

    op.execute("DELETE FROM travel_document_chunks_fts")
    op.execute(
        "INSERT INTO travel_document_chunks_fts "
        "(id, document_id, country_code, primary_destination_slug, text) "
        "SELECT id, source_document_id, country_code, destination_slug, search_text "
        "FROM document_chunks"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_travel_chunks_fts_insert "
        "AFTER INSERT ON document_chunks BEGIN "
        "INSERT INTO travel_document_chunks_fts "
        "(id, document_id, country_code, primary_destination_slug, text) "
        "VALUES (new.id, new.source_document_id, new.country_code, "
        "new.destination_slug, replace(replace(new.text, 'đ', 'd'), 'Đ', 'D')); END"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_travel_chunks_fts_delete "
        "AFTER DELETE ON document_chunks BEGIN "
        "DELETE FROM travel_document_chunks_fts WHERE id = old.id; END"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_travel_chunks_fts_update "
        "AFTER UPDATE OF id, source_document_id, country_code, destination_slug, text, search_text "
        "ON document_chunks BEGIN "
        "DELETE FROM travel_document_chunks_fts WHERE id = old.id; "
        "INSERT INTO travel_document_chunks_fts "
        "(id, document_id, country_code, primary_destination_slug, text) "
        "VALUES (new.id, new.source_document_id, new.country_code, "
        "new.destination_slug, replace(replace(new.text, 'đ', 'd'), 'Đ', 'D')); END"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_travel_chunks_fts_update")
        op.execute("DROP TRIGGER IF EXISTS trg_travel_chunks_fts_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_travel_chunks_fts_insert")
    op.execute("UPDATE document_chunks SET search_text = lower(text)")
