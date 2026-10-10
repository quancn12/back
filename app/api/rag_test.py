"""
Endpoint test riêng phần retrieval (RAG) — giúp kiểm tra chất lượng
tìm kiếm ngữ nghĩa TRƯỚC khi ghép vào luồng chat đầy đủ (bước sau).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_configured_admin
from app.models.hotel import Hotel
from app.services.vector_store import vector_store

router = APIRouter(prefix="/api/hotels", tags=["rag-test"])


class RagQueryRequest(BaseModel):
    question: str
    top_k: int = 5


@router.post("/{slug}/rag-test", dependencies=[Depends(require_configured_admin)])
async def rag_test(slug: str, payload: RagQueryRequest, db: Session = Depends(get_db)):
    hotel = db.query(Hotel).filter(Hotel.slug == slug).first()
    if not hotel:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách sạn.")

    results = await vector_store.query(
        hotel_id=hotel.id, query_text=payload.question, top_k=payload.top_k
    )
    return {"question": payload.question, "results": results}
