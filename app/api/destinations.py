"""Destination catalog API for VietSage."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.models.destination import Destination
from app.models.schemas import DestinationCreate, DestinationOut


router = APIRouter(prefix="/api/destinations", tags=["destinations"])


# Catalogue writes are an operator action. This route had no guard at all;
# nothing in the app calls it (destinations are seeded at startup), so
# requiring the admin key costs no legitimate caller anything.
@router.post("", response_model=DestinationOut, dependencies=[Depends(require_admin)])
def create_destination(payload: DestinationCreate, db: Session = Depends(get_db)):
    if db.query(Destination).filter(Destination.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail=f"Slug '{payload.slug}' đã tồn tại.")
    if payload.parent_id and not db.query(Destination).filter(
        Destination.id == payload.parent_id
    ).first():
        raise HTTPException(status_code=400, detail="parent_id không tồn tại.")
    destination = Destination(**payload.model_dump())
    db.add(destination)
    db.commit()
    db.refresh(destination)
    return destination


@router.get("", response_model=list[DestinationOut])
def list_destinations(
    country_code: str = "VN",
    db: Session = Depends(get_db),
):
    return db.query(Destination).filter(
        Destination.country_code == country_code.upper()
    ).order_by(Destination.name).all()


@router.get("/{slug}", response_model=DestinationOut)
def get_destination(slug: str, db: Session = Depends(get_db)):
    destination = db.query(Destination).filter(Destination.slug == slug).first()
    if destination is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy điểm đến.")
    return destination

