"""Disposable PostgreSQL/pgvector coverage; run via scripts/run_postgres_integration.py."""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.models.destination import Destination
from app.models.knowledge import (
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    SourceDocument,
)
from app.services.travel_ingestion import _claim_next_job
from app.services.travel_vector_store import TravelVectorStore


ENABLED = (
    os.environ.get("POSTGRES_INTEGRATION") == "1"
    and engine.dialect.name == "postgresql"
)


@unittest.skipUnless(ENABLED, "requires disposable PostgreSQL/pgvector database")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not settings.database_url.startswith("postgresql"):
            raise RuntimeError("POSTGRES_INTEGRATION requires a PostgreSQL DATABASE_URL")
        cls.prefix = f"it-{uuid.uuid4()}"
        cls.destination_id = str(uuid.uuid4())
        cls.retrieval_ids: list[str] = []
        cls.job_ids: list[str] = []
        with SessionLocal() as db:
            db.add(
                Destination(
                    id=cls.destination_id,
                    slug=cls.prefix,
                    name="Integration destination",
                    destination_type="region",
                    country_code="VN",
                    country_name="Việt Nam",
                )
            )
            db.flush()
            source = SourceDocument(
                id=str(uuid.uuid4()),
                source_key=f"{cls.prefix}-source",
                primary_destination_id=cls.destination_id,
                filename="integration.pdf",
                storage_path="/tmp/integration.pdf",
                status="active",
            )
            version = DocumentVersion(
                id=str(uuid.uuid4()),
                source_document_id=source.id,
                version=1,
                content_hash=cls.prefix,
                storage_path=source.storage_path,
                status="done",
                is_active=True,
                embedding_model="test",
                chunker_version="test",
            )
            db.add(source)
            db.flush()
            db.add(version)
            db.flush()
            dimension = settings.embedding_dimensions
            close_vector = [1.0] + [0.0] * (dimension - 1)
            far_vector = [0.0, 1.0] + [0.0] * (dimension - 2)
            chunks = [
                DocumentChunk(
                    id=f"{cls.prefix}-near",
                    source_document_id=source.id,
                    document_version_id=version.id,
                    chunk_index=0,
                    section="Overview",
                    text="Ha Long Bay has limestone islands and clear water.",
                    search_text="Ha Long Bay has limestone islands and clear water.",
                    embedding=close_vector,
                    country_code="VN",
                    destination_slug=cls.prefix,
                    destination_name="Integration destination",
                    filename="integration.pdf",
                    chunk_metadata={"source": "integration.pdf"},
                ),
                DocumentChunk(
                    id=f"{cls.prefix}-far",
                    source_document_id=source.id,
                    document_version_id=version.id,
                    chunk_index=1,
                    section="Overview",
                    text="A remote forest route crosses quiet hills.",
                    search_text="A remote forest route crosses quiet hills.",
                    embedding=far_vector,
                    country_code="VN",
                    destination_slug=cls.prefix,
                    destination_name="Integration destination",
                    filename="integration.pdf",
                    chunk_metadata={"source": "integration.pdf"},
                ),
            ]
            db.add_all(chunks)
            cls.retrieval_ids = [chunk.id for chunk in chunks]
            cls._create_job(db, cls, 1)
            cls._create_job(db, cls, 2)
            db.commit()

    @staticmethod
    def _create_job(db, cls, index: int) -> None:
        source = SourceDocument(
            id=str(uuid.uuid4()),
            source_key=f"{cls.prefix}-queue-{index}",
            primary_destination_id=cls.destination_id,
            filename=f"queue-{index}.pdf",
            storage_path=f"/tmp/queue-{index}.pdf",
            status="active",
        )
        version = DocumentVersion(
            id=str(uuid.uuid4()),
            source_document_id=source.id,
            version=1,
            content_hash=f"{cls.prefix}-{index}",
            storage_path=source.storage_path,
            status="pending",
            is_active=False,
            embedding_model="test",
            chunker_version="test",
            created_at=datetime.utcnow() + timedelta(seconds=index),
        )
        job = IngestionJob(
            id=str(uuid.uuid4()),
            source_document_id=source.id,
            document_version_id=version.id,
            status="pending",
            attempts=0,
            created_at=datetime.utcnow() + timedelta(seconds=index),
        )
        db.add(source)
        db.flush()
        db.add(version)
        db.flush()
        db.add(job)
        db.flush()
        cls.job_ids.append(job.id)

    @classmethod
    def tearDownClass(cls) -> None:
        if not ENABLED or not getattr(cls, "retrieval_ids", None):
            return
        with SessionLocal() as db:
            db.query(DocumentChunk).filter(DocumentChunk.id.in_(cls.retrieval_ids)).delete(
                synchronize_session=False
            )
            db.query(IngestionJob).filter(IngestionJob.id.in_(cls.job_ids)).delete(
                synchronize_session=False
            )
            source_ids = db.scalars(
                select(SourceDocument.id).where(SourceDocument.source_key.like(f"{cls.prefix}%"))
            ).all()
            version_ids = db.scalars(
                select(DocumentVersion.id).where(
                    DocumentVersion.source_document_id.in_(source_ids)
                )
            ).all()
            db.query(DocumentVersion).filter(DocumentVersion.id.in_(version_ids)).delete(
                synchronize_session=False
            )
            db.query(SourceDocument).filter(SourceDocument.id.in_(source_ids)).delete(
                synchronize_session=False
            )
            db.query(Destination).filter(Destination.id == cls.destination_id).delete(
                synchronize_session=False
            )
            db.commit()
        engine.dispose()

    def test_pgvector_extension_and_indexes_exist(self) -> None:
        with engine.connect() as conn:
            extension = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
            indexes = {
                row.indexname: row.indexdef
                for row in conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = current_schema() AND tablename = 'document_chunks'"
                    )
                ).mappings()
            }
        self.assertIsNotNone(extension)
        self.assertIn("ix_document_chunks_embedding_hnsw", indexes)
        self.assertIn("vector_cosine_ops", indexes["ix_document_chunks_embedding_hnsw"])
        self.assertIn("ix_document_chunks_search_fts", indexes)
        self.assertIn("using gin", indexes["ix_document_chunks_search_fts"].casefold())

    def test_pgvector_retrieval_returns_nearest_active_chunk(self) -> None:
        vector = [1.0] + [0.0] * (settings.embedding_dimensions - 1)
        candidates = TravelVectorStore()._query_postgres(
            "Ha Long Bay islands",
            vector,
            "VN",
            self.prefix,
            1,
        )
        ranked = TravelVectorStore._rank(candidates, 1, "Ha Long Bay islands")
        self.assertEqual([f"{self.prefix}-near"], [item["id"] for item in ranked])
        self.assertAlmostEqual(0.0, ranked[0]["distance"], places=5)

    def test_skip_locked_claims_distinct_jobs_and_then_releases_first(self) -> None:
        locker = SessionLocal()

        def claim():
            db = SessionLocal()
            try:
                return _claim_next_job(db)
            finally:
                db.close()

        try:
            first = locker.execute(
                select(IngestionJob)
                .where(IngestionJob.id == self.job_ids[0])
                .with_for_update()
            ).scalar_one()
            claimed = claim()
            self.assertEqual(self.job_ids[1], claimed.id)
            self.assertEqual(1, claimed.attempts)
            locker.commit()
            next_job = claim()
            self.assertEqual(first.id, next_job.id)
            self.assertEqual(1, next_job.attempts)
        finally:
            locker.close()


if __name__ == "__main__":
    unittest.main()
