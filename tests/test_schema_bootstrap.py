"""Runtime startup validates migrations and never creates application tables."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.core import schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SchemaBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "startup.db"
        self.engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        self.engine_patch = patch.object(schema, "engine", self.engine)
        self.engine_patch.start()

    def tearDown(self) -> None:
        self.engine_patch.stop()
        self.engine.dispose()
        self.tempdir.cleanup()

    def test_missing_revision_fails_without_creating_tables(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no Alembic revision"):
            schema.initialize_database()
        self.assertEqual([], inspect(self.engine).get_table_names())

    def test_outdated_revision_fails_with_upgrade_instructions(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES ('20260923_0007')")
            )

        with self.assertRaisesRegex(
            RuntimeError, "Run `python -m alembic upgrade head`"
        ):
            schema.initialize_database()

    def test_current_head_is_accepted_without_runtime_ddl(self) -> None:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        scripts = ScriptDirectory.from_config(config)
        head = scripts.get_current_head()
        with self.engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                {"head": head},
            )

        schema.initialize_database()
        self.assertEqual(["alembic_version"], inspect(self.engine).get_table_names())

    def test_fresh_sqlite_migration_creates_travel_fts5(self) -> None:
        db_path = Path(self.tempdir.name) / "fresh-migrations.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

        env["PYTHONPATH"] = str(PROJECT_ROOT)
        startup_result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.core.schema import initialize_database; initialize_database()",
            ],
            cwd=self.tempdir.name,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            0,
            startup_result.returncode,
            startup_result.stdout + startup_result.stderr,
        )

        migrated_engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        try:
            with migrated_engine.connect() as conn:
                fts_table = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'travel_document_chunks_fts'"
                    )
                ).scalar_one_or_none()
                revision = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            self.assertEqual("travel_document_chunks_fts", fts_table)
            self.assertEqual("20260924_0013", revision)
        finally:
            migrated_engine.dispose()


if __name__ == "__main__":
    unittest.main()
