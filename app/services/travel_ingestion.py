"""Durable, idempotent ingestion queue and atomic document-version processor."""
from __future__ import annotations

import asyncio
import math
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.destination import Destination
from app.models.knowledge import (
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    SourceDocument,
)
from app.services.llm_client import llm_client
from app.services.source_storage import file_sha256, source_storage
from app.services.travel_document_processor import CHUNKER_VERSION, process_travel_pdf
from app.services.travel_vector_store import normalize_search_text


def _utcnow() -> datetime:
    return datetime.utcnow()


def _source_key(value: str) -> str:
    return value.replace("\\", "/").strip().casefold()


DEMO_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "demo"
DEMO_DATASET_ID = "vietsage-demo-facts-2026-09"


def _has_demo_tag(value: str | None) -> bool:
    """Return true for an explicit, case-insensitive ``demo`` path/tag token."""
    if not value:
        return False
    return "demo" in {part for part in re.split(r"[\\/:._-]+", value.casefold()) if part}


def _resolved_path_is_demo(path: Path) -> bool:
    """Check the resolved path, so a symlink cannot escape the demo root."""
    resolved = path.resolve(strict=False)
    root = DEMO_DATA_ROOT.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


def source_provenance(
    *,
    path: Path,
    source_key: str,
    filename: str | None = None,
    provenance: str | None = None,
) -> str:
    """Classify a source and reject a demo tag outside the isolated root.

    The resolver runs before storage copying. A tagged path outside
    ``data/demo`` fails closed and is never moved automatically.
    """
    path_is_demo = _resolved_path_is_demo(path)
    tagged_demo = (
        (provenance or "").casefold() == "demo"
        or _has_demo_tag(source_key)
        or _has_demo_tag(filename)
        or path_is_demo
    )
    if tagged_demo and not path_is_demo:
        raise ValueError(
            "Demo source must resolve under data/demo; relocate it explicitly "
            "before ingestion."
        )
    return "demo" if tagged_demo else "real"


def _canonical_ingestion_key(source_key: str, provenance: str) -> str:
    canonical = _source_key(source_key)
    if provenance == "demo" and not canonical.startswith("demo:"):
        return f"demo:{canonical}"
    return canonical


def _managed_key_variants(source_keys: set[str]) -> set[str]:
    variants: set[str] = set()
    for value in source_keys:
        canonical = _source_key(value)
        variants.add(canonical)
        if not canonical.startswith("demo:"):
            variants.add(f"demo:{canonical}")
    return variants


def enqueue_pdf_path(
    *,
    path: Path,
    destination: Destination,
    db: Session,
    source_key: str,
    filename: str | None = None,
    managed_source_keys: set[str] | None = None,
    provenance: str | None = None,
) -> tuple[IngestionJob | None, DocumentVersion, bool]:
    """Persist the source and enqueue a revision. Returns (job, version, changed)."""
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Chỉ hỗ trợ PDF: {path.name}")

    display_filename = filename or path.name
    resolved_provenance = source_provenance(
        path=path,
        source_key=source_key,
        filename=display_filename,
        provenance=provenance,
    )
    canonical_key = _canonical_ingestion_key(source_key, resolved_provenance)
    content_hash = file_sha256(path)
    source = db.query(SourceDocument).filter(
        SourceDocument.source_key == canonical_key
    ).first()
    if source is None and managed_source_keys is not None:
        # A same-content source whose previous path disappeared is a rename, not a new doc.
        source = (
            db.query(SourceDocument)
            .join(DocumentVersion, DocumentVersion.source_document_id == SourceDocument.id)
            .filter(
                DocumentVersion.content_hash == content_hash,
                SourceDocument.source_key.like("%folder:%"),
                SourceDocument.source_key.notin_(_managed_key_variants(managed_source_keys)),
            )
            .order_by(DocumentVersion.is_active.desc(), DocumentVersion.version.desc())
            .first()
        )
        if source is not None:
            source.source_key = canonical_key
    if source is None:
        source = SourceDocument(
            source_key=canonical_key,
            primary_destination_id=destination.id,
            filename=display_filename,
            storage_path="",
            status="active",
        )
        db.add(source)
        db.flush()
    else:
        source.primary_destination_id = destination.id
        source.filename = display_filename
        source.status = "active"
        source.deleted_at = None

    existing = db.query(DocumentVersion).filter(
        DocumentVersion.source_document_id == source.id,
        DocumentVersion.content_hash == content_hash,
        DocumentVersion.embedding_model == settings.ollama_embed_model,
        DocumentVersion.chunker_version == CHUNKER_VERSION,
    ).first()
    stored_path = source_storage.store(path, canonical_key, content_hash)
    source.storage_path = str(stored_path)
    if existing is not None:
        source.updated_at = _utcnow()
        db.commit()
        job = db.query(IngestionJob).filter(
            IngestionJob.document_version_id == existing.id,
            IngestionJob.status.in_(("pending", "processing")),
        ).first()
        return job, existing, False

    next_version = (
        db.query(func.max(DocumentVersion.version))
        .filter(DocumentVersion.source_document_id == source.id)
        .scalar()
        or 0
    ) + 1
    version = DocumentVersion(
        source_document_id=source.id,
        version=next_version,
        content_hash=content_hash,
        storage_path=str(stored_path),
        status="pending",
        is_active=False,
        embedding_model=settings.ollama_embed_model,
        chunker_version=CHUNKER_VERSION,
        source_modified_at=datetime.fromtimestamp(path.stat().st_mtime),
    )
    db.add(version)
    db.flush()
    job = IngestionJob(
        source_document_id=source.id,
        document_version_id=version.id,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(version)
    db.refresh(job)
    return job, version, True


def _claim_next_job(db: Session) -> IngestionJob | None:
    now = _utcnow()
    stale_before = now - timedelta(seconds=settings.ingestion_job_lease_seconds)
    last_heartbeat = func.coalesce(
        IngestionJob.heartbeat_at,
        IngestionJob.claimed_at,
        IngestionJob.started_at,
        IngestionJob.created_at,
    )
    stale_processing = and_(
        IngestionJob.status == "processing",
        last_heartbeat < stale_before,
    )

    # A crashed final attempt must become visibly failed rather than remaining
    # "processing" forever. Keep the version aligned with the terminal job.
    exhausted_message = "Ingestion worker lease expired after the final attempt."
    # Lock/update the queue row first, matching the finalizer's lock order.
    # RETURNING captures only rows that remain stale when the lock is acquired.
    exhausted_version_ids = db.execute(
        update(IngestionJob)
        .where(
            stale_processing,
            IngestionJob.attempts >= settings.ingestion_max_attempts,
        )
        .values(
            status="failed",
            error_message=exhausted_message,
            completed_at=now,
            claim_token=None,
        )
        .returning(IngestionJob.document_version_id)
    ).scalars().all()
    if exhausted_version_ids:
        db.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id.in_(exhausted_version_ids))
            .values(status="failed", error_message=exhausted_message)
        )
    db.commit()

    claimable = or_(IngestionJob.status == "pending", stale_processing)
    claim_token = str(uuid.uuid4())
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "sqlite":
        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise RuntimeError(
                "Atomic SQLite ingestion claims require SQLite 3.35 or newer "
                "for UPDATE ... RETURNING. Upgrade the SQLite runtime before "
                "enabling ingestion workers."
            )
        # SQLite has no row-level FOR UPDATE. Claim with one conditional UPDATE
        # so concurrent connections cannot both commit the same pending row.
        pending_job_id = (
            select(IngestionJob.id)
            .where(
                claimable,
                IngestionJob.attempts < settings.ingestion_max_attempts,
            )
            .order_by(IngestionJob.created_at, IngestionJob.id)
            .limit(1)
            .scalar_subquery()
        )
        claimed_id = db.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == pending_job_id,
                claimable,
                IngestionJob.attempts < settings.ingestion_max_attempts,
            )
            .values(
                status="processing",
                attempts=IngestionJob.attempts + 1,
                started_at=now,
                claimed_at=now,
                heartbeat_at=now,
                claim_token=claim_token,
                completed_at=None,
                error_message=None,
            )
            .returning(IngestionJob.id)
        ).scalar_one_or_none()
        if claimed_id is None:
            return None
        db.commit()
        return db.get(IngestionJob, claimed_id)

    statement = select(IngestionJob).where(
        claimable,
        IngestionJob.attempts < settings.ingestion_max_attempts,
    ).order_by(IngestionJob.created_at)
    if dialect_name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    job = db.execute(statement.limit(1)).scalar_one_or_none()
    if job is None:
        return None
    job.status = "processing"
    job.attempts += 1
    job.started_at = now
    job.claimed_at = now
    job.heartbeat_at = now
    job.claim_token = claim_token
    job.completed_at = None
    job.error_message = None
    db.commit()
    db.refresh(job)
    return job


def _renew_job_lease(job_id: str, claim_token: str) -> bool:
    """Renew one owned claim using a separate short-lived transaction."""
    db = SessionLocal()
    try:
        renewed = db.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.status == "processing",
                IngestionJob.claim_token == claim_token,
            )
            .values(heartbeat_at=_utcnow())
        ).rowcount
        db.commit()
        return renewed == 1
    finally:
        db.close()


async def _heartbeat_job_lease(
    job_id: str,
    claim_token: str,
    stop: asyncio.Event,
) -> None:
    """Renew a live claim until processing enters its short final transaction."""
    while True:
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.ingestion_job_heartbeat_seconds
            )
            return
        except TimeoutError:
            renewed = await asyncio.to_thread(
                _renew_job_lease, job_id, claim_token
            )
            if not renewed:
                raise RuntimeError("Ingestion job lease ownership was lost.")


async def _embed_chunks(texts: list[str]) -> list[list[float]]:
    embeddings: list[list[float]] = []
    batch_size = max(1, settings.ollama_embed_batch_size)
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        vectors = await llm_client.embed_batch(batch)
        for vector in vectors:
            if len(vector) != settings.embedding_dimensions:
                raise RuntimeError(
                    f"Embedding dimension {len(vector)} khác cấu hình "
                    f"{settings.embedding_dimensions}."
                )
            if not all(math.isfinite(value) for value in vector):
                raise RuntimeError("Embedding chứa giá trị không hữu hạn.")
        embeddings.extend(vectors)
    return embeddings


async def process_job(job_id: str, claim_token: str | None = None) -> IngestionJob:
    """Process a claimed job; the active version only switches at final commit."""
    db = SessionLocal()
    heartbeat_stop: asyncio.Event | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        job = db.get(IngestionJob, job_id)
        if job is None:
            raise ValueError(f"Không tìm thấy ingestion job {job_id}")
        if claim_token is not None:
            if job.status != "processing" or job.claim_token != claim_token:
                raise RuntimeError("Ingestion job lease ownership was lost.")
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                _heartbeat_job_lease(job_id, claim_token, heartbeat_stop)
            )
        version = db.get(DocumentVersion, job.document_version_id)
        source = db.get(SourceDocument, job.source_document_id)
        if version is None or source is None:
            raise RuntimeError("Job tham chiếu source/version không tồn tại.")
        destination = db.get(Destination, source.primary_destination_id)
        if destination is None:
            raise RuntimeError("Điểm đến của tài liệu không tồn tại.")

        processed = await asyncio.to_thread(
            process_travel_pdf, version.storage_path, source.filename
        )
        prepared = [chunk for chunk in processed.chunks if chunk.text.strip()]
        if not prepared:
            raise RuntimeError("Tài liệu không tạo được chunk hữu ích.")
        embeddings = await _embed_chunks([chunk.text for chunk in prepared])

        if heartbeat_task is not None and heartbeat_stop is not None:
            heartbeat_stop.set()
            await heartbeat_task
            heartbeat_task = None
            db.expire_all()
            # Fence the final write transaction with the current claim token.
            # This UPDATE locks the queue row (or SQLite writer database) before
            # any chunks/version state is changed. A reclaimed worker therefore
            # makes this transaction fail closed instead of letting stale work
            # overwrite the new owner's result.
            fenced = db.execute(
                update(IngestionJob)
                .where(
                    IngestionJob.id == job_id,
                    IngestionJob.status == "processing",
                    IngestionJob.claim_token == claim_token,
                )
                .values(heartbeat_at=_utcnow())
                .execution_options(synchronize_session=False)
            ).rowcount
            if fenced != 1:
                raise RuntimeError("Ingestion job lease ownership was lost.")
            job = db.get(IngestionJob, job_id)
            if (
                job is None
                or job.status != "processing"
                or job.claim_token != claim_token
            ):
                raise RuntimeError("Ingestion job lease ownership was lost.")
            version = db.get(DocumentVersion, job.document_version_id)
            source = db.get(SourceDocument, job.source_document_id)
            if version is None or source is None:
                raise RuntimeError("Job tham chiếu source/version không tồn tại.")

        # Parsing and embedding are complete. The active-version switch is atomic.
        db.query(DocumentChunk).filter(
            DocumentChunk.document_version_id == version.id
        ).delete(synchronize_session=False)
        for index, (chunk, embedding) in enumerate(zip(prepared, embeddings)):
            metadata = {
                "document_id": source.id,
                "document_version_id": version.id,
                "source": source.filename,
                "filename": source.filename,
                "section": chunk.section,
                "chunk_index": index,
                "country_code": destination.country_code,
                "primary_destination_slug": destination.slug,
                "primary_destination_name": destination.name,
                "locations": " | ".join(processed.locations),
                "embedding_model": settings.ollama_embed_model,
                "chunker_version": CHUNKER_VERSION,
                "provenance": "demo" if source.source_key.startswith("demo:") else "real",
                "synthetic": source.source_key.startswith("demo:"),
                "authority": "non-authoritative" if source.source_key.startswith("demo:") else "source",
                "dataset_id": DEMO_DATASET_ID if source.source_key.startswith("demo:") else None,
            }
            db.add(
                DocumentChunk(
                    id=f"{version.id}-{index}",
                    source_document_id=source.id,
                    document_version_id=version.id,
                    chunk_index=index,
                    section=chunk.section,
                    text=chunk.text,
                    search_text=normalize_search_text(chunk.text),
                    embedding=embedding,
                    country_code=destination.country_code,
                    destination_slug=destination.slug,
                    destination_name=destination.name,
                    province_name=destination.province_name,
                    filename=source.filename,
                    locations=" | ".join(processed.locations),
                    chunk_metadata=metadata,
                )
            )

        db.execute(
            update(DocumentVersion)
            .where(
                DocumentVersion.source_document_id == source.id,
                DocumentVersion.id != version.id,
            )
            .values(is_active=False)
        )
        version.status = "done"
        version.is_active = True
        version.error_message = None
        version.chunk_count = len(prepared)
        version.tour_code = processed.tour_code
        version.duration_days = processed.duration_days
        version.locations = " | ".join(processed.locations)
        version.processed_at = _utcnow()
        source.status = "active"
        source.updated_at = _utcnow()
        job.status = "done"
        job.claim_token = None
        job.error_message = None
        job.completed_at = _utcnow()
        db.commit()
        db.refresh(job)
        return job
    except Exception as exc:
        db.rollback()
        failed_job = db.get(IngestionJob, job_id)
        if failed_job is not None:
            if claim_token is not None and failed_job.claim_token != claim_token:
                raise
            failed_job.status = (
                "pending"
                if failed_job.attempts < settings.ingestion_max_attempts
                else "failed"
            )
            failed_job.error_message = str(exc)
            failed_job.claim_token = None
            failed_job.heartbeat_at = None
            failed_job.completed_at = _utcnow() if failed_job.status == "failed" else None
            failed_version = db.get(DocumentVersion, failed_job.document_version_id)
            if failed_version is not None:
                failed_version.status = failed_job.status
                failed_version.error_message = str(exc)
            db.commit()
        raise
    finally:
        if heartbeat_task is not None:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            try:
                await heartbeat_task
            except Exception:
                # The main path already records the actionable processing
                # failure. Never mask it with a shutdown heartbeat exception.
                pass
        db.close()


async def run_one_pending_job() -> IngestionJob | None:
    db = SessionLocal()
    try:
        job = _claim_next_job(db)
        job_id = job.id if job else None
        claim_token = job.claim_token if job else None
    finally:
        db.close()
    if job_id is None:
        return None
    return await process_job(job_id, claim_token)


async def run_pending_jobs(stop_when_empty: bool = True) -> tuple[int, int]:
    completed = failed = 0
    await llm_client.startup()
    try:
        while True:
            try:
                job = await run_one_pending_job()
            except Exception:
                failed += 1
                await asyncio.sleep(settings.ingestion_poll_seconds)
                continue
            if job is None:
                if stop_when_empty:
                    break
                await asyncio.sleep(settings.ingestion_poll_seconds)
                continue
            completed += 1
    finally:
        await llm_client.shutdown()
    return completed, failed


def mark_missing_sources_deleted(db: Session, expected_source_keys: set[str]) -> int:
    """Tombstone folder-managed sources that disappeared from the current scan."""
    managed = db.query(SourceDocument).filter(
        SourceDocument.source_key.like("%folder:%"),
        SourceDocument.status != "deleted",
    ).all()
    expected = _managed_key_variants(expected_source_keys)
    missing = [item for item in managed if item.source_key not in expected]
    now = _utcnow()
    for source in missing:
        source.status = "deleted"
        source.deleted_at = now
        db.query(DocumentVersion).filter(
            DocumentVersion.source_document_id == source.id
        ).update({DocumentVersion.is_active: False}, synchronize_session=False)
    db.commit()
    return len(missing)
