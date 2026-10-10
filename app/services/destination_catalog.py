"""Seed and lookup helpers for the initial VietSage destination catalog."""
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.destination import Destination
from app.services.travel_document_processor import normalize_vietnamese


def slugify_destination(value: str) -> str:
    return normalize_vietnamese(value).replace(" ", "-") or "viet-nam"


def ensure_destination(
    db: Session,
    *,
    slug: str,
    name: str,
    province_name: str | None = None,
    destination_type: str = "region",
) -> Destination:
    """Create a discovered Vietnamese destination without code changes."""
    initial = ensure_initial_destinations(db)
    destination = db.query(Destination).filter(Destination.slug == slug).first()
    if destination is not None:
        return destination
    destination = Destination(
        slug=slug,
        name=name,
        destination_type=destination_type,
        country_code=settings.default_country_code,
        country_name=settings.default_country_name,
        province_name=province_name or name,
        parent_id=initial["viet-nam"].id,
    )
    db.add(destination)
    db.commit()
    db.refresh(destination)
    return destination


def ensure_initial_destinations(db: Session) -> dict[str, Destination]:
    country = db.query(Destination).filter(Destination.slug == "viet-nam").first()
    if country is None:
        country = Destination(
            slug="viet-nam",
            name=settings.default_country_name,
            destination_type="country",
            country_code=settings.default_country_code,
            country_name=settings.default_country_name,
            aliases="VN | Vietnam | Việt Nam",
        )
        db.add(country)
        db.flush()

    yen_bai = db.query(Destination).filter(Destination.slug == "yen-bai").first()
    if yen_bai is None:
        yen_bai = Destination(
            slug="yen-bai",
            name="Yên Bái",
            destination_type="region",
            country_code=settings.default_country_code,
            country_name=settings.default_country_name,
            province_name="Yên Bái",
            parent_id=country.id,
            aliases="Yen Bai",
        )
        db.add(yen_bai)
        db.flush()

    lake = db.query(Destination).filter(Destination.slug == "ho-thac-ba").first()
    if lake is None:
        lake = Destination(
            slug="ho-thac-ba",
            name="Hồ Thác Bà",
            destination_type="lake",
            country_code=settings.default_country_code,
            country_name=settings.default_country_name,
            province_name="Yên Bái",
            parent_id=yen_bai.id,
            aliases="Ho Thac Ba | Thac Ba Lake",
        )
        db.add(lake)

    db.commit()
    return {"viet-nam": country, "yen-bai": yen_bai, "ho-thac-ba": lake}


def choose_destination_slug(filename: str, fallback: str = "yen-bai") -> str:
    normalized = normalize_vietnamese(filename)
    if "ho thac ba" in normalized or normalized.startswith("hvtb"):
        return "ho-thac-ba"
    return fallback
