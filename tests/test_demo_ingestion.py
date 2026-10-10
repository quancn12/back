"""Isolated tests for the synthetic PDF/data-mode boundary."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.main import api_info
from app.models.destination import Destination
from app.models.knowledge import (
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    SourceDocument,
)
from app.services import travel_ingestion as ingestion
from app.services.source_storage import LocalSourceStorage
from scripts.ingest_travel_data import discover_pdfs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_PDF = PROJECT_ROOT / "data" / "demo" / "corpus" / "demo_facts.pdf"


class DemoIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.engine = create_engine(
            f"sqlite:///{(self.root / 'demo.db').as_posix()}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.storage = LocalSourceStorage(self.root / "sources")
        self.db = self.Session()
        self.destination = Destination(
            slug="demo-hoa-mau",
            name="Hoa Mẫu (demo)",
            destination_type="fictional_region",
            country_code="VN",
            country_name="Việt Nam",
            province_name="Tỉnh mô phỏng",
        )
        self.db.add(self.destination)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    def _patch_runtime(self):
        return patch.object(
            ingestion,
            "SessionLocal",
            self.Session,
        ), patch.object(ingestion, "source_storage", self.storage)

    def test_public_api_info_uses_explicit_mode_and_fails_closed_in_production(self):
        with patch.object(settings, "data_mode", "demo"), patch.object(
            settings, "app_env", "development"
        ):
            self.assertEqual(asyncio.run(api_info())["data_mode"], "demo")
        with patch.object(settings, "data_mode", "demo"), patch.object(
            settings, "app_env", "production"
        ):
            self.assertEqual(asyncio.run(api_info())["data_mode"], "real")

    async def _fake_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * ingestion.settings.embedding_dimensions for _ in texts]

    def test_real_pdf_ingest_is_isolated_idempotent_and_provenance_tagged(self):
        self.assertTrue(DEMO_PDF.is_file())
        session_patch, storage_patch = self._patch_runtime()
        with session_patch, storage_patch, patch.object(
            ingestion, "_embed_chunks", self._fake_embeddings
        ):
            job, version, changed = ingestion.enqueue_pdf_path(
                path=DEMO_PDF,
                destination=self.destination,
                db=self.db,
                source_key="folder:demo_facts.pdf",
                filename=DEMO_PDF.name,
            )
            self.assertTrue(changed)
            self.assertIsNotNone(job)
            asyncio.run(ingestion.process_job(job.id))
            self.db.expire_all()

            first_version_id = version.id
            first_count = self.db.query(DocumentChunk).count()
            source = self.db.query(SourceDocument).one()
            done_version = self.db.query(DocumentVersion).one()
            self.assertEqual(done_version.status, "done")
            self.assertTrue(done_version.is_active)
            self.assertGreater(first_count, 0)
            self.assertTrue(source.source_key.startswith("demo:"))
            metadata = self.db.query(DocumentChunk).first().chunk_metadata
            self.assertEqual(metadata["provenance"], "demo")
            self.assertTrue(metadata["synthetic"])
            self.assertEqual(metadata["authority"], "non-authoritative")

            second_job, second_version, second_changed = ingestion.enqueue_pdf_path(
                path=DEMO_PDF,
                destination=self.destination,
                db=self.db,
                source_key="folder:demo_facts.pdf",
                filename=DEMO_PDF.name,
            )
            self.assertFalse(second_changed)
            self.assertEqual(second_version.id, first_version_id)
            self.assertIsNone(second_job)
            self.assertEqual(self.db.query(DocumentVersion).count(), 1)
            self.assertEqual(self.db.query(DocumentChunk).count(), first_count)

    def test_demo_tag_outside_root_is_rejected_before_storage_copy(self):
        outside_pdf = self.root / "outside.pdf"
        outside_pdf.write_bytes(DEMO_PDF.read_bytes())
        session_patch, storage_patch = self._patch_runtime()
        with session_patch, storage_patch:
            with self.assertRaisesRegex(ValueError, "under data/demo"):
                ingestion.enqueue_pdf_path(
                    path=outside_pdf,
                    destination=self.destination,
                    db=self.db,
                    source_key="demo:outside.pdf",
                    filename="outside.pdf",
                )
        self.assertEqual(self.db.query(SourceDocument).count(), 0)
        self.assertFalse((self.root / "sources").exists())

    def test_sqlite_concurrent_claimers_take_distinct_jobs(self):
        created_at = datetime.utcnow()
        job_ids = []
        for index in range(2):
            source = SourceDocument(
                source_key=f"claim-test-{index}.pdf",
                primary_destination_id=self.destination.id,
                filename=f"claim-test-{index}.pdf",
                storage_path=str(self.root / f"claim-test-{index}.pdf"),
            )
            self.db.add(source)
            self.db.flush()
            version = DocumentVersion(
                source_document_id=source.id,
                version=1,
                content_hash=f"claim-test-{index}",
                storage_path=source.storage_path,
                status="pending",
                is_active=False,
                embedding_model="test",
                chunker_version="test",
            )
            self.db.add(version)
            self.db.flush()
            job = IngestionJob(
                source_document_id=source.id,
                document_version_id=version.id,
                status="pending",
                attempts=0,
                created_at=created_at + timedelta(seconds=index),
            )
            self.db.add(job)
            self.db.flush()
            job_ids.append(job.id)
        self.db.commit()

        start = Barrier(2)

        def claim_from_independent_session():
            db = self.Session()
            try:
                start.wait(timeout=5)
                job = ingestion._claim_next_job(db)
                return job.id if job else None
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed_ids = list(
                pool.map(lambda _: claim_from_independent_session(), range(2))
            )

        self.assertCountEqual(job_ids, claimed_ids)
        self.db.expire_all()
        claimed = self.db.query(IngestionJob).filter(IngestionJob.id.in_(job_ids)).all()
        self.assertEqual({"processing"}, {job.status for job in claimed})
        self.assertEqual({1}, {job.attempts for job in claimed})

    def test_sqlite_claim_requires_update_returning_support(self):
        with patch.object(ingestion.sqlite3, "sqlite_version_info", (3, 34, 0)):
            with self.assertRaisesRegex(RuntimeError, "SQLite 3.35 or newer"):
                ingestion._claim_next_job(self.db)

    def test_stale_processing_job_is_reclaimed_after_worker_death(self):
        source = SourceDocument(
            source_key="stale-lease.pdf",
            primary_destination_id=self.destination.id,
            filename="stale-lease.pdf",
            storage_path=str(self.root / "stale-lease.pdf"),
        )
        self.db.add(source)
        self.db.flush()
        version = DocumentVersion(
            source_document_id=source.id,
            version=1,
            content_hash="stale-lease",
            storage_path=source.storage_path,
            status="processing",
            is_active=False,
            embedding_model="test",
            chunker_version="test",
        )
        self.db.add(version)
        self.db.flush()
        stale_at = datetime.utcnow() - timedelta(minutes=10)
        job = IngestionJob(
            source_document_id=source.id,
            document_version_id=version.id,
            status="processing",
            attempts=1,
            started_at=stale_at,
            claimed_at=stale_at,
            heartbeat_at=stale_at,
            claim_token="dead-worker",
        )
        self.db.add(job)
        self.db.commit()

        with patch.object(settings, "ingestion_job_lease_seconds", 60):
            reclaimed = ingestion._claim_next_job(self.db)

        self.assertEqual(job.id, reclaimed.id)
        self.assertEqual(2, reclaimed.attempts)
        self.assertEqual("processing", reclaimed.status)
        self.assertNotEqual("dead-worker", reclaimed.claim_token)
        self.assertGreater(reclaimed.heartbeat_at, stale_at)

    def test_fresh_processing_heartbeat_is_not_reclaimed(self):
        source = SourceDocument(
            source_key="fresh-lease.pdf",
            primary_destination_id=self.destination.id,
            filename="fresh-lease.pdf",
            storage_path=str(self.root / "fresh-lease.pdf"),
        )
        self.db.add(source)
        self.db.flush()
        version = DocumentVersion(
            source_document_id=source.id,
            version=1,
            content_hash="fresh-lease",
            storage_path=source.storage_path,
            status="processing",
            is_active=False,
            embedding_model="test",
            chunker_version="test",
        )
        self.db.add(version)
        self.db.flush()
        now = datetime.utcnow()
        job = IngestionJob(
            source_document_id=source.id,
            document_version_id=version.id,
            status="processing",
            attempts=1,
            started_at=now,
            claimed_at=now,
            heartbeat_at=now,
            claim_token="live-worker",
        )
        self.db.add(job)
        self.db.commit()

        with patch.object(settings, "ingestion_job_lease_seconds", 60):
            self.assertIsNone(ingestion._claim_next_job(self.db))
        self.db.refresh(job)
        self.assertEqual("live-worker", job.claim_token)
        self.assertEqual("processing", job.status)

    def test_exhausted_stale_job_becomes_failed_instead_of_stuck(self):
        source = SourceDocument(
            source_key="exhausted-lease.pdf",
            primary_destination_id=self.destination.id,
            filename="exhausted-lease.pdf",
            storage_path=str(self.root / "exhausted-lease.pdf"),
        )
        self.db.add(source)
        self.db.flush()
        version = DocumentVersion(
            source_document_id=source.id,
            version=1,
            content_hash="exhausted-lease",
            storage_path=source.storage_path,
            status="processing",
            is_active=False,
            embedding_model="test",
            chunker_version="test",
        )
        self.db.add(version)
        self.db.flush()
        stale_at = datetime.utcnow() - timedelta(minutes=10)
        job = IngestionJob(
            source_document_id=source.id,
            document_version_id=version.id,
            status="processing",
            attempts=settings.ingestion_max_attempts,
            started_at=stale_at,
            claimed_at=stale_at,
            heartbeat_at=stale_at,
            claim_token="dead-final-worker",
        )
        self.db.add(job)
        self.db.commit()

        with patch.object(settings, "ingestion_job_lease_seconds", 60):
            self.assertIsNone(ingestion._claim_next_job(self.db))
        self.db.refresh(job)
        self.db.refresh(version)
        self.assertEqual("failed", job.status)
        self.assertEqual("failed", version.status)
        self.assertIsNone(job.claim_token)
        self.assertIsNotNone(job.completed_at)

    def test_broad_real_scan_excludes_demo_tree_but_explicit_demo_root_includes_it(self):
        manifest = self.root / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        broad = discover_pdfs(PROJECT_ROOT / "data", manifest)
        self.assertTrue(
            all(
                DEMO_PDF.parent not in item.path.parents and item.path != DEMO_PDF
                for item in broad
            )
        )
        explicit = discover_pdfs(PROJECT_ROOT / "data" / "demo" / "corpus", PROJECT_ROOT / "data" / "demo" / "manifest.json")
        self.assertEqual([item.path.resolve() for item in explicit], [DEMO_PDF.resolve()])


if __name__ == "__main__":
    unittest.main()
