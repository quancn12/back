"""Idempotently seed the synthetic hotel used by the unified MVP demo."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.hotel import Hotel
from app.models.unified import HotelKnowledgeItem, HotelRoom, HotelServiceItem, Stay, utcnow
from app.services.stay_access import hash_pin, secret_hash


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_SOURCE = PROJECT_ROOT / "demo_data" / "vietsage_demo_source.json"
DEMO_HOTEL_SLUG = "an-nhien-son-tra-demo"

DEMO_ROOMS = {
    "305": {"connect_code": "demo-an-nhien-305", "pin": "3056"},
    "410": {"connect_code": "demo-an-nhien-410", "pin": "4106"},
    "208": {"connect_code": "demo-an-nhien-208", "pin": "2086"},
}


def _load_source() -> dict:
    with DEMO_SOURCE.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def seed_unified_demo(db: Session) -> Hotel:
    data = _load_source()
    hotel_data = data["hotel"]
    hotel = db.query(Hotel).filter(Hotel.slug == DEMO_HOTEL_SLUG).first()
    if hotel is None:
        hotel = Hotel(
            id=hotel_data["id"],
            slug=DEMO_HOTEL_SLUG,
            name=hotel_data["name"],
            city=hotel_data["city"],
            latitude=hotel_data["latitude"],
            longitude=hotel_data["longitude"],
            default_language="vi",
            supported_languages="vi,en",
            created_at=utcnow(),
        )
        db.add(hotel)
        db.flush()

    knowledge_specs: list[tuple[str, str, str, str]] = []
    for index, content in enumerate(hotel_data["public_information"]):
        knowledge_specs.append((f"public-{index}", "hotel", "Thông tin khách sạn", content))
    for index, content in enumerate(hotel_data["policies"]):
        knowledge_specs.append((f"policy-{index}", "policy", "Chính sách lưu trú", content))
    knowledge_specs.append(
        (
            "verified-wifi",
            "wifi",
            "Thông tin Wi-Fi",
            f"Tên mạng {hotel_data['wifi']['ssid']}. Mật khẩu {hotel_data['wifi']['password']}.",
            "VERIFIED_STAY",
        )
    )
    for place in data["places"]:
        indoor = "trong nhà" if place["indoor"] else "ngoài trời"
        content = (
            f"{place['name']} cách khách sạn {place['distance_from_hotel_km']} km, "
            f"{indoor}. {place['notes']}"
        )
        knowledge_specs.append((f"place-{place['id']}", "travel", place["name"], content))

    for raw in knowledge_specs:
        code, category, title, content, *visibility = raw
        item_id = f"demo-knowledge-{code}"
        item = db.get(HotelKnowledgeItem, item_id)
        if item is None:
            db.add(
                HotelKnowledgeItem(
                    id=item_id,
                    hotel_id=hotel.id,
                    category=category,
                    title=title,
                    content=content,
                    visibility=visibility[0] if visibility else "PUBLIC",
                )
            )

    for service in data["service_catalog"]:
        item = db.query(HotelServiceItem).filter(
            HotelServiceItem.hotel_id == hotel.id,
            HotelServiceItem.code == service["id"],
        ).first()
        if item is None:
            db.add(
                HotelServiceItem(
                    id=f"demo-{service['id']}",
                    hotel_id=hotel.id,
                    code=service["id"],
                    category=service["category"],
                    name=service["name"],
                    description=f"Thời gian phục vụ: {service['hours']}.",
                    price=service["price"],
                    currency=service["currency"],
                    hours=service["hours"],
                    estimated_minutes=service["estimated_minutes"],
                    requires_confirmation=service["requires_confirmation"],
                )
            )

    now = utcnow()
    for room_number, credentials in DEMO_ROOMS.items():
        room = db.query(HotelRoom).filter(
            HotelRoom.hotel_id == hotel.id,
            HotelRoom.room_number == room_number,
        ).first()
        if room is None:
            room = HotelRoom(
                id=f"demo-room-{room_number}",
                hotel_id=hotel.id,
                room_number=room_number,
                connect_code_hash=secret_hash(credentials["connect_code"]),
                verification_pin_hash=hash_pin(credentials["pin"]),
            )
            db.add(room)
            db.flush()
        elif not room.verification_pin_hash.startswith("pbkdf2_sha256$"):
            room.verification_pin_hash = hash_pin(credentials["pin"])
        stay = db.query(Stay).filter(
            Stay.room_id == room.id,
            Stay.status == "ACTIVE",
        ).first()
        if stay is None:
            db.add(
                Stay(
                    id=f"demo-stay-{room_number}",
                    hotel_id=hotel.id,
                    room_id=room.id,
                    guest_alias=f"Khách demo phòng {room_number}",
                    check_in_at=now - timedelta(hours=2),
                    check_out_at=now + timedelta(days=2),
                    status="ACTIVE",
                )
            )
        else:
            # Keep the demo usable across days without touching real stay records.
            stay.check_in_at = now - timedelta(hours=2)
            stay.check_out_at = now + timedelta(days=2)

    db.commit()
    return hotel


def demo_scenarios() -> list[dict]:
    return [
        {
            "room_number": room,
            "connect_code": credentials["connect_code"],
            "pin": credentials["pin"],
            "hotel_slug": DEMO_HOTEL_SLUG,
        }
        for room, credentials in DEMO_ROOMS.items()
    ]
