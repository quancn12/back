"""Opaque QR connection challenges and scoped stay access grants."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.hotel import Hotel
from app.models.unified import (
    HotelRoom,
    Stay,
    StayAccessGrant,
    StayConnectionChallenge,
    utcnow,
)
from app.models.unified_schemas import ActiveStayOut


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_pin(pin: str, *, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    iterations = 210_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), bytes.fromhex(salt), iterations
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def pin_matches(pin: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            pin.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations_text),
        ).hex()
        return hmac.compare_digest(digest, expected)
    except (TypeError, ValueError):
        return False


# Wrong PINs are counted per ROOM, across every challenge. A challenge costs
# nothing to create -- anyone holding the room's connect code, which is printed
# on a QR in the room, gets a fresh one per scan -- so a counter on the
# challenge reset itself each time and the five-try limit was decorative.
#
# Two windows, both read from existing columns (no migration): a short one so a
# guest who mistypes is back in a few minutes, and a daily cap so that the short
# window cannot be ridden indefinitely. At 20 tries a day, walking a 4-digit PIN
# space takes months rather than seconds.
PIN_LOCKOUT_RULES = (
    (timedelta(minutes=15), 5),
    (timedelta(hours=24), 20),
)
_PIN_LOCKED_DETAIL = (
    "Đã nhập sai PIN quá nhiều lần cho phòng này. "
    "Vui lòng thử lại sau hoặc liên hệ lễ tân."
)


def _room_pin_locked(db: Session, room: HotelRoom) -> bool:
    now = utcnow()
    for window, limit in PIN_LOCKOUT_RULES:
        since = now - window
        # Reception's unlock: earlier wrong PINs stay on record but stop counting.
        if room.pin_lock_cleared_at is not None and room.pin_lock_cleared_at > since:
            since = room.pin_lock_cleared_at
        failures = db.query(
            func.coalesce(func.sum(StayConnectionChallenge.failed_attempts), 0)
        ).filter(
            StayConnectionChallenge.room_id == room.id,
            StayConnectionChallenge.created_at >= since,
        ).scalar()
        if failures >= limit:
            return True
    return False


def _current_stay(db: Session, room_id: str, now) -> Stay | None:
    return db.query(Stay).filter(
        Stay.room_id == room_id,
        Stay.status == "ACTIVE",
        Stay.check_in_at <= now,
        Stay.check_out_at >= now,
    ).order_by(Stay.check_out_at.desc()).first()


def create_connection_challenge(db: Session, connect_code: str):
    room = db.query(HotelRoom).filter(
        HotelRoom.connect_code_hash == secret_hash(connect_code.strip()),
        HotelRoom.is_active.is_(True),
    ).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Mã kết nối không hợp lệ.")
    # Refuse before handing out a new challenge: that is exactly the step the
    # old per-challenge counter let an attacker repeat.
    if _room_pin_locked(db, room):
        raise HTTPException(status_code=429, detail=_PIN_LOCKED_DETAIL)
    hotel = db.get(Hotel, room.hotel_id)
    if hotel is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách sạn.")
    challenge = StayConnectionChallenge(
        room_id=room.id,
        expires_at=utcnow() + timedelta(minutes=10),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return challenge, hotel


def verify_connection_challenge(db: Session, connection_id: str, pin: str):
    challenge = db.get(StayConnectionChallenge, connection_id)
    now = utcnow()
    if challenge is None or challenge.used_at is not None or challenge.expires_at <= now:
        raise HTTPException(status_code=410, detail="Phiên xác minh đã hết hạn.")
    room = db.get(HotelRoom, challenge.room_id)
    if room is None or not room.is_active:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng.")
    # Checked before the PIN is compared, so even a correct guess is refused
    # while the room is locked -- otherwise the lock would only slow down
    # wrong answers.
    if _room_pin_locked(db, room):
        raise HTTPException(status_code=429, detail=_PIN_LOCKED_DETAIL)

    stay = _current_stay(db, room.id, now)
    # A stay with its own PIN no longer accepts the room's standing PIN. "No
    # active stay" is still only said after a correct PIN, so the QR alone does
    # not reveal whether the room is occupied.
    expected = stay.verification_pin_hash if stay and stay.verification_pin_hash else room.verification_pin_hash
    if not pin_matches(pin.strip(), expected):
        challenge.failed_attempts += 1
        db.commit()
        raise HTTPException(status_code=401, detail="PIN không đúng.")
    if stay is None:
        raise HTTPException(status_code=404, detail="Phòng không có phiên lưu trú đang hoạt động.")

    raw_token = secrets.token_urlsafe(32)
    expires_at = min(
        now + timedelta(hours=settings.stay_access_ttl_hours),
        stay.check_out_at,
    )
    grant = StayAccessGrant(
        stay_id=stay.id,
        token_hash=secret_hash(raw_token),
        expires_at=expires_at,
    )
    challenge.used_at = now
    db.add(grant)
    db.commit()
    db.refresh(grant)
    hotel = db.get(Hotel, stay.hotel_id)
    assert hotel is not None
    return raw_token, grant, stay, room, hotel


# Reception operations. Rooms are named the way staff know them -- hotel slug
# and room number -- and both calls sit behind the operator key in the API.
STAY_PIN_DIGITS = 6


def _room_by_number(db: Session, hotel_slug: str, room_number: str) -> HotelRoom:
    room = db.query(HotelRoom).join(Hotel, Hotel.id == HotelRoom.hotel_id).filter(
        Hotel.slug == hotel_slug,
        HotelRoom.room_number == room_number,
        HotelRoom.is_active.is_(True),
    ).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng.")
    return room


def issue_stay_pin(
    db: Session, hotel_slug: str, room_number: str, *, revoke_devices: bool = False
) -> tuple[str, Stay, int]:
    """Give the stay in progress its own random PIN and return it once.

    Only the hash is stored. From now on the room's standing PIN no longer opens
    this stay. ``revoke_devices`` also signs out every device already connected
    to it, for a PIN that leaked mid-stay.
    """
    room = _room_by_number(db, hotel_slug, room_number)
    now = utcnow()
    stay = _current_stay(db, room.id, now)
    if stay is None:
        raise HTTPException(status_code=404, detail="Phòng không có phiên lưu trú đang hoạt động.")
    pin = f"{secrets.randbelow(10 ** STAY_PIN_DIGITS):0{STAY_PIN_DIGITS}d}"
    stay.verification_pin_hash = hash_pin(pin)
    revoked = 0
    if revoke_devices:
        grants = db.query(StayAccessGrant).filter(
            StayAccessGrant.stay_id == stay.id,
            StayAccessGrant.revoked_at.is_(None),
            StayAccessGrant.expires_at > now,
        ).all()
        for grant in grants:
            grant.revoked_at = now
        revoked = len(grants)
    db.commit()
    db.refresh(stay)
    return pin, stay, revoked


def clear_room_pin_lock(db: Session, hotel_slug: str, room_number: str) -> tuple[HotelRoom, int]:
    """Lift a PIN lockout. Earlier wrong PINs stay on record but stop counting.

    Open challenges are closed too: they were created before the unlock, so
    their failures would no longer count and they could be used to keep
    guessing until they expire.
    """
    room = _room_by_number(db, hotel_slug, room_number)
    now = utcnow()
    room.pin_lock_cleared_at = now
    open_challenges = db.query(StayConnectionChallenge).filter(
        StayConnectionChallenge.room_id == room.id,
        StayConnectionChallenge.used_at.is_(None),
        StayConnectionChallenge.expires_at > now,
    ).all()
    for challenge in open_challenges:
        challenge.expires_at = now
    db.commit()
    db.refresh(room)
    return room, len(open_challenges)


def resolve_stay_token(
    db: Session,
    raw_token: str | None,
    *,
    required_scope: str | None = None,
    optional: bool = False,
):
    if not raw_token:
        if optional:
            return None
        raise HTTPException(status_code=401, detail="Thiếu quyền truy cập lưu trú.")
    now = utcnow()
    grant = db.query(StayAccessGrant).filter(
        StayAccessGrant.token_hash == secret_hash(raw_token),
        StayAccessGrant.revoked_at.is_(None),
        StayAccessGrant.expires_at > now,
    ).first()
    if grant is None:
        if optional:
            return None
        raise HTTPException(status_code=401, detail="Quyền truy cập lưu trú đã hết hạn.")
    scopes = {scope.strip() for scope in grant.scopes.split(",") if scope.strip()}
    if required_scope and required_scope not in scopes:
        raise HTTPException(status_code=403, detail="Quyền truy cập không đủ.")
    stay = db.get(Stay, grant.stay_id)
    if stay is None or stay.status != "ACTIVE" or stay.check_out_at <= now:
        raise HTTPException(status_code=401, detail="Phiên lưu trú không còn hoạt động.")
    room = db.get(HotelRoom, stay.room_id)
    hotel = db.get(Hotel, stay.hotel_id)
    if room is None or hotel is None:
        raise HTTPException(status_code=404, detail="Thiếu dữ liệu lưu trú.")
    return grant, stay, room, hotel, scopes


def active_stay_out(grant, stay, room, hotel, scopes) -> ActiveStayOut:
    def as_utc(value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    return ActiveStayOut(
        id=stay.id,
        hotel_slug=hotel.slug,
        hotel_name=hotel.name,
        room_number=room.room_number,
        guest_alias=stay.guest_alias,
        check_in_at=as_utc(stay.check_in_at),
        check_out_at=as_utc(stay.check_out_at),
        scopes=sorted(scopes),
    )
