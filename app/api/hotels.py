"""API quản lý khách sạn — tạo khách sạn mới, xem danh sách/chi tiết."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.models.hotel import Hotel
from app.models.schemas import HotelCreate, HotelOut

router = APIRouter(prefix="/api/hotels", tags=["hotels"])


@router.post("", response_model=HotelOut, dependencies=[Depends(require_admin)])
def create_hotel(payload: HotelCreate, db: Session = Depends(get_db)):
    existing = db.query(Hotel).filter(Hotel.slug == payload.slug).first()
    if existing:
        raise HTTPException(
            status_code=400, detail=f"Slug '{payload.slug}' đã tồn tại."
        )

    hotel = Hotel(**payload.model_dump())
    db.add(hotel)
    db.commit()
    db.refresh(hotel)
    return hotel


@router.get("", response_model=list[HotelOut])
def list_hotels(db: Session = Depends(get_db)):
    return db.query(Hotel).all()


@router.get("/{slug}", response_model=HotelOut)
def get_hotel(slug: str, db: Session = Depends(get_db)):
    hotel = db.query(Hotel).filter(Hotel.slug == slug).first()
    if not hotel:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách sạn.")
    return hotel
