"""Validate that the database has been upgraded by Alembic before use."""
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.database import engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def initialize_database() -> None:
    """Fail fast when the configured database is not at the Alembic head.

    Runtime startup must never create or repair application tables. Schema DDL
    belongs to versioned Alembic revisions and is applied explicitly before the
    app or ingestion CLI starts.
    """
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()

    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()

    if len(heads) != 1:
        expected = ", ".join(heads) or "<none>"
        raise RuntimeError(
            f"Alembic must have exactly one head (found: {expected}); "
            "resolve the migration graph before starting VietSage."
        )
    if current != heads[0]:
        current_label = current or "<no Alembic revision>"
        raise RuntimeError(
            "Database schema is not at the current Alembic head "
            f"(current: {current_label}; expected: {heads[0]}). Run "
            "`python -m alembic upgrade head` before starting VietSage. "
            "Runtime startup does not create or repair database tables."
        )
