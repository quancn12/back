"""A database the old runtime touched must still upgrade to head.

Until 20260923_0008 the app ran ``Base.metadata.create_all()`` at startup. The
online launcher (host_local.ps1) started the app without migrating, so a
machine that pulled new models and went online got the new tables created
behind Alembic's back while ``alembic_version`` stayed behind. When startup
began refusing un-migrated databases, ``alembic upgrade head`` then failed with
"table ... already exists" and the app could not start at all. Reproduced on a
copy of a real development database before the fix.

These tests rebuild that state from scratch: migrate to 0004, let create_all
run with every current model, then upgrade.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import (  # noqa: F401  (register every table create_all knew)
    auth,
    destination,
    hotel,
    knowledge,
    proactive,
    travel_document,
    unified,
)
from app.models.destination import Destination
from app.models.knowledge import DocumentChunk, DocumentVersion, SourceDocument
from app.services.travel_vector_store import TravelVectorStore

ROOT = Path(__file__).resolve().parents[1]
LAST_REVISION_BEFORE_CREATE_ALL_WINDOW = "20260911_0004"


def _head() -> str:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _schema(db_path: Path) -> dict[str, set[str]]:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        inspector = inspect(engine)
        return {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in inspector.get_table_names()
            if table != "alembic_version" and not table.startswith("sqlite_")
            and "_fts" not in table
        }
    finally:
        engine.dispose()


def _current(db_path: Path) -> str | None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as connection:
            return connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar()
    finally:
        engine.dispose()


class MigrationAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _migrate(self, db_path: Path, target: str) -> None:
        result = _alembic(db_path, "upgrade", target)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def _touch_with_old_runtime(self, db_path: Path) -> None:
        # What the old startup did on every launch.
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        try:
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()

    def test_a_database_touched_by_create_all_upgrades_to_head(self):
        db = self.base / "adopted.db"
        self._migrate(db, LAST_REVISION_BEFORE_CREATE_ALL_WINDOW)
        self._touch_with_old_runtime(db)

        result = _alembic(db, "upgrade", "head")

        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertNotIn("already exists", result.stderr)
        self.assertEqual(_current(db), _head())

    def test_ownership_columns_are_added_even_though_the_auth_tables_existed(self):
        # create_all() creates missing tables but never alters existing ones,
        # so the conversation table keeps lacking its owner columns.
        db = self.base / "adopted.db"
        self._migrate(db, LAST_REVISION_BEFORE_CREATE_ALL_WINDOW)
        self._touch_with_old_runtime(db)
        self.assertNotIn("owner_session_id", _schema(db)["unified_conversations"])

        self._migrate(db, "head")

        columns = _schema(db)["unified_conversations"]
        self.assertIn("owner_user_id", columns)
        self.assertIn("owner_session_id", columns)

    def test_the_adopted_schema_matches_a_fresh_install(self):
        fresh = self.base / "fresh.db"
        adopted = self.base / "adopted.db"
        self._migrate(fresh, "head")
        self._migrate(adopted, LAST_REVISION_BEFORE_CREATE_ALL_WINDOW)
        self._touch_with_old_runtime(adopted)
        self._migrate(adopted, "head")

        fresh_schema = _schema(fresh)
        adopted_schema = _schema(adopted)
        self.assertEqual(set(adopted_schema), set(fresh_schema))
        for table, columns in fresh_schema.items():
            with self.subTest(table=table):
                self.assertEqual(adopted_schema[table], columns)

    def test_a_fresh_install_is_unchanged(self):
        db = self.base / "fresh.db"
        result = _alembic(db, "upgrade", "head")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(_current(db), _head())
        self.assertIn("message_feedback", _schema(db))
        self.assertIn("app_sessions", _schema(db))

    def test_fts_upgrade_backfills_and_triggers_keep_the_index_current(self):
        db_path = self.base / "fts.db"
        self._migrate(db_path, "20260923_0009")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        try:
            with Session(engine) as db:
                destination_row = Destination(
                    slug="ho-thac-ba",
                    name="Hồ Thác Bà",
                    destination_type="lake",
                    country_code="VN",
                    country_name="Việt Nam",
                )
                db.add(destination_row)
                db.flush()
                source = SourceDocument(
                    source_key="fts-hvtb.pdf",
                    primary_destination_id=destination_row.id,
                    filename="HVTB 0004-23.pdf",
                    storage_path="/tmp/fts-hvtb.pdf",
                )
                db.add(source)
                db.flush()
                version = DocumentVersion(
                    source_document_id=source.id,
                    version=1,
                    content_hash="fts-hvtb",
                    storage_path=source.storage_path,
                    status="done",
                    is_active=True,
                    embedding_model="test",
                    chunker_version="test",
                )
                db.add(version)
                db.flush()
                chunk = DocumentChunk(
                    id="fts-hvtb-0",
                    source_document_id=source.id,
                    document_version_id=version.id,
                    chunk_index=0,
                    text="Chương trình HVTB 0004-23 tham quan Hồ Thác Bà, có thể đổi lịch.",
                    search_text="chương trình hvtb 0004-23 tham quan hồ thác bà, có thể đổi lịch.",
                    embedding=[0.0] * 1024,
                    country_code="VN",
                    destination_slug="ho-thac-ba",
                    destination_name="Hồ Thác Bà",
                    filename=source.filename,
                    chunk_metadata={"source": source.filename},
                )
                db.add(chunk)
                db.commit()
        finally:
            engine.dispose()

        self._migrate(db_path, "head")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        try:
            with Session(engine) as db:
                rows = TravelVectorStore._query_indexed_keywords(
                    db, "HVTB 0004", "VN", "ho-thac-ba"
                )
                self.assertEqual(["fts-hvtb-0"], [row["id"] for row in rows])
                unaccented = TravelVectorStore._query_indexed_keywords(
                    db, "doi lich", "VN", "ho-thac-ba"
                )
                self.assertEqual(["fts-hvtb-0"], [row["id"] for row in unaccented])

                chunk = db.get(DocumentChunk, "fts-hvtb-0")
                chunk.text = "Chương trình đổi sang Suối Giàng."
                db.commit()
                old_rows = TravelVectorStore._query_indexed_keywords(
                    db, "HVTB 0004", "VN", "ho-thac-ba"
                )
                new_rows = TravelVectorStore._query_indexed_keywords(
                    db, "Suối Giàng", "VN", "ho-thac-ba"
                )
                self.assertEqual([], old_rows)
                self.assertEqual(["fts-hvtb-0"], [row["id"] for row in new_rows])

                db.delete(chunk)
                db.commit()
                self.assertEqual(
                    [],
                    TravelVectorStore._query_indexed_keywords(
                        db, "Suối Giàng", "VN", "ho-thac-ba"
                    ),
                )
        finally:
            engine.dispose()


class LegacyHotelSchemaTests(unittest.TestCase):
    """B6: the legacy Hotel DDL lives in revision 0011, not in app startup."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _migrate(self, db_path: Path, target: str) -> None:
        result = _alembic(db_path, *(["downgrade"] if target.startswith("-") else ["upgrade"]), target.lstrip("-"))
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def _hotel_shape(self, db_path: Path) -> tuple[set[str], set[str], bool]:
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        try:
            inspector = inspect(engine)
            columns = {column["name"] for column in inspector.get_columns("hotel_documents")}
            indexes = {index["name"] for index in inspector.get_indexes("hotel_documents")}
            with engine.connect() as conn:
                fts = conn.exec_driver_sql(
                    "SELECT 1 FROM sqlite_master WHERE name = 'hotel_document_chunks_fts'"
                ).scalar() is not None
            return columns, indexes, fts
        finally:
            engine.dispose()

    def test_a_fresh_install_has_the_legacy_hotel_search_table(self):
        db = self.base / "fresh.db"
        self._migrate(db, "head")
        columns, indexes, fts = self._hotel_shape(db)
        self.assertIn("content_hash", columns)
        self.assertIn("ix_hotel_documents_content_hash", indexes)
        self.assertTrue(fts)

    def test_an_old_hotel_table_without_content_hash_is_brought_up_to_date(self):
        db = self.base / "old.db"
        self._migrate(db, "20260923_0010")
        engine = create_engine(f"sqlite:///{db.as_posix()}")
        try:
            with engine.begin() as conn:
                # The shape a pre-content_hash create_all() left behind.
                conn.exec_driver_sql("DROP INDEX ix_hotel_documents_content_hash")
                conn.exec_driver_sql("ALTER TABLE hotel_documents DROP COLUMN content_hash")
        finally:
            engine.dispose()
        self._migrate(db, "head")
        columns, indexes, fts = self._hotel_shape(db)
        self.assertIn("content_hash", columns)
        self.assertIn("ix_hotel_documents_content_hash", indexes)
        self.assertTrue(fts)

    def test_downgrade_removes_only_the_search_table_and_upgrade_restores_it(self):
        db = self.base / "roundtrip.db"
        self._migrate(db, "head")
        self._migrate(db, "-20260923_0010")
        columns, _, fts = self._hotel_shape(db)
        self.assertIn("content_hash", columns)
        self.assertFalse(fts)
        self._migrate(db, "head")
        self.assertTrue(self._hotel_shape(db)[2])

    def test_startup_no_longer_changes_the_schema(self):
        main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        for ddl in ("ALTER TABLE", "CREATE VIRTUAL TABLE", "CREATE INDEX", "create_all("):
            with self.subTest(ddl=ddl):
                self.assertNotIn(ddl, main)


if __name__ == "__main__":
    unittest.main()
