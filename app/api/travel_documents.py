"""Durable travel-document administration and ingestion-job endpoints."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.models.destination import Destination
from app.models.knowledge import DocumentVersion, IngestionJob, SourceDocument
from app.models.schemas import (
    DocumentVersionOut,
    EnqueueDocumentResponse,
    IngestionJobOut,
    SourceDocumentOut,
)
from app.services.source_storage import safe_key
from app.services.travel_ingestion import enqueue_pdf_path


router = APIRouter(tags=["travel-documents"])


@router.post(
    "/api/destinations/{slug}/documents",
    response_model=EnqueueDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
)
async def upload_travel_document(
    slug: str,
    file: UploadFile = File(...),
    source_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    destination = db.query(Destination).filter(Destination.slug == slug).first()
    if destination is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy điểm đến.")
    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file PDF.")

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_path = temp_file.name
            while block := await file.read(1024 * 1024):
                temp_file.write(block)
        job, version, changed = enqueue_pdf_path(
            path=Path(temp_path),
            destination=destination,
            db=db,
            filename=filename,
            source_key=source_key or f"upload:{slug}:{safe_key(filename)}",
        )
        source = db.get(SourceDocument, version.source_document_id)
        assert source is not None
        return EnqueueDocumentResponse(
            changed=changed,
            source=source,
            version=version,
            job=job,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@router.get(
    "/api/destinations/{slug}/documents",
    response_model=list[SourceDocumentOut],
)
def list_travel_documents(
    slug: str,
    include_deleted: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    destination = db.query(Destination).filter(Destination.slug == slug).first()
    if destination is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy điểm đến.")
    query = db.query(SourceDocument).filter(
        SourceDocument.primary_destination_id == destination.id
    )
    if not include_deleted:
        query = query.filter(SourceDocument.status != "deleted")
    return query.order_by(SourceDocument.updated_at.desc()).offset(offset).limit(limit).all()


@router.get(
    "/api/documents/{document_id}/versions",
    response_model=list[DocumentVersionOut],
)
def list_document_versions(document_id: str, db: Session = Depends(get_db)):
    if db.get(SourceDocument, document_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    return db.query(DocumentVersion).filter(
        DocumentVersion.source_document_id == document_id
    ).order_by(DocumentVersion.version.desc()).all()


@router.delete(
    "/api/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_document(document_id: str, db: Session = Depends(get_db)) -> None:
    source = db.get(SourceDocument, document_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    source.status = "deleted"
    source.deleted_at = datetime.utcnow()
    db.query(DocumentVersion).filter(
        DocumentVersion.source_document_id == source.id
    ).update({DocumentVersion.is_active: False}, synchronize_session=False)
    db.commit()


@router.get("/api/ingestion-jobs", response_model=list[IngestionJobOut])
def list_ingestion_jobs(
    job_status: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(IngestionJob)
    if job_status:
        query = query.filter(IngestionJob.status == job_status)
    return query.order_by(IngestionJob.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/api/ingestion-jobs/{job_id}", response_model=IngestionJobOut)
def get_ingestion_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy ingestion job.")
    return job
