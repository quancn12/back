"""Run PostgreSQL integration checks against an empty, disposable database."""

from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text


def main() -> int:
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        print(
            "POSTGRES_TEST_DATABASE_URL must point to a disposable PostgreSQL database.",
            file=sys.stderr,
        )
        return 2
    database_name = urlsplit(url).path.removeprefix("/").split("?", 1)[0]
    if not database_name.casefold().endswith(("_test", "_b3")):
        print(
            "Refusing a database without a '_test' or '_b3' suffix; use a disposable database.",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = current_schema()"
                )
            ).scalars().all()
            if tables:
                print(
                    "Refusing to run migrations: database is not empty ("
                    + ", ".join(tables)
                    + "). Use a fresh disposable database.",
                    file=sys.stderr,
                )
                return 2
    finally:
        engine.dispose()

    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["POSTGRES_INTEGRATION"] = "1"
    alembic = [sys.executable, "-m", "alembic"]
    migration = subprocess.run([*alembic, "upgrade", "head"], env=env, check=False)
    if migration.returncode:
        return migration.returncode
    tests = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_postgres_integration.py",
            "-v",
        ],
        env=env,
        check=False,
    )
    if tests.returncode:
        return tests.returncode

    # Model databases where the old runtime created the HNSW index before
    # Alembic recorded revision 0007. The 0007 migration must adopt that index
    # idempotently instead of failing on a name collision.
    downgrade = subprocess.run(
        [*alembic, "downgrade", "20260922_0006"], env=env, check=False
    )
    if downgrade.returncode:
        return downgrade.returncode
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX ix_document_chunks_embedding_hnsw "
                    "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
                )
            )
    finally:
        engine.dispose()
    print("Checking migration 0007 against a 0006 database with a legacy HNSW index.")
    upgrade = subprocess.run([*alembic, "upgrade", "head"], env=env, check=False)
    return upgrade.returncode


if __name__ == "__main__":
    raise SystemExit(main())
