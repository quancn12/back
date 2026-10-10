"""
Upload PDF for a hotel, extract text, chunk it, embed it, and store vectors.
"""
import asyncio
import hashlib
import os
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.core.security import require_admin
from app.models.hotel import Hotel
from app.models.hotel_document import HotelDocument
from app.models.schemas import HotelDocumentOut
from app.services.document_processor import process_pdf
from app.services.vector_store import vector_store

router = APIRouter(prefix="/api/hotels", tags=["documents"])


async def process_document_in_background(
    doc_id: str, hotel_id: str, tmp_path: str, filename: str
) -> None:
    db: Session = SessionLocal()
    try:
        # Chạy CPU-heavy process_pdf trên worker thread để tránh block event loop
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, process_pdf, tmp_path)
        if not chunks:
            raise ValueError(
                "Không trích xuất được nội dung văn bản nào từ PDF. Tệp có thể là ảnh quét và cần OCR."
            )

        added_count = await vector_store.add_chunks(
            hotel_id=hotel_id,
            document_id=doc_id,
            chunks=chunks,
            filename=filename,
        )

        doc = db.query(HotelDocument).filter(HotelDocument.id == doc_id).first()
        if doc:
            doc.status = "done"
            doc.chunk_count = added_count
            doc.processed_at = datetime.utcnow()
            db.commit()

    except Exception as e:
        doc = db.query(HotelDocument).filter(HotelDocument.id == doc_id).first()
        if doc:
            doc.status = "failed"
            doc.error_message = str(e)
            db.commit()
    finally:
        db.close()
        # Dọn dẹp file tạm
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as ce:
                print(f"Error removing temp file {tmp_path}: {ce}")


def _save_upload_and_hash(file: UploadFile) -> tuple[str, str]:
    """Save UploadFile to a temp path while calculating a whole-file SHA-256."""
    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_path = tmp_file.name
        while True:
            block = file.file.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            tmp_file.write(block)
    return tmp_path, digest.hexdigest()


@router.post(
    "/{slug}/documents",
    response_model=HotelDocumentOut,
    dependencies=[Depends(require_admin)],
)
async def upload_document(
    slug: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    hotel = db.query(Hotel).filter(Hotel.slug == slug).first()
    if not hotel:
        raise HTTPException(status_code=404, detail="Khong tim thay khach san.")

    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Chi ho tro file PDF.")

    tmp_path = None
    try:
        tmp_path, content_hash = _save_upload_and_hash(file)

        duplicate = (
            db.query(HotelDocument)
            .filter(
                HotelDocument.hotel_id == hotel.id,
                HotelDocument.content_hash == content_hash,
                HotelDocument.status == "done",
            )
            .first()
        )
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail=(
                    "File PDF nay da duoc upload va embedding truoc do. "
                    f"Document trung: {duplicate.filename}."
                ),
            )

        doc = HotelDocument(
            hotel_id=hotel.id,
            filename=filename,
            content_hash=content_hash,
            status="processing",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        # Chạy tác vụ xử lý PDF và tạo vector embedding ngầm
        background_tasks.add_task(
            process_document_in_background,
            doc.id,
            hotel.id,
            tmp_path,
            filename,
        )

        return doc

    except HTTPException:
        # Nếu lỗi HTTP xảy ra trước khi tạo bản ghi, dọn dẹp file tạm ngay lập tức
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    except Exception as e:  # noqa: BLE001
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/{slug}/documents", response_model=list[HotelDocumentOut])
def list_documents(slug: str, db: Session = Depends(get_db)):
    hotel = db.query(Hotel).filter(Hotel.slug == slug).first()
    if not hotel:
        raise HTTPException(status_code=404, detail="Khong tim thay khach san.")
    return db.query(HotelDocument).filter(HotelDocument.hotel_id == hotel.id).all()
