"""Reception's two PIN operations: a PIN per stay, and lifting a lockout.

Before this, the PIN belonged to the room and never changed between guests,
and a room locked by wrong PINs (A4) stayed locked for up to a day with no way
for reception to help. Both calls sit behind the operator key.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth, unified
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import auth as auth_models  # noqa: F401
from app.models import destination, hotel, knowledge  # noqa: F401
from app.models.unified import Stay
from app.services.demo_seed import DEMO_HOTEL_SLUG, seed_unified_demo

ROOM = {"code": "demo-an-nhien-305", "pin": "3056", "number": "305"}
KEY = "reception-test-key"
WRONG_PIN = "0000"


class ReceptionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        db = self.Session()
        try:
            seed_unified_demo(db)
        finally:
            db.close()
        app = FastAPI()
        app.include_router(auth.router)
        app.include_router(unified.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.client.headers["X-CSRF-Token"] = self.client.get("/api/v1/auth/session").json()["csrf_token"]
        self.key = patch.object(settings, "admin_api_key", KEY)
        self.key.start()

    def tearDown(self):
        self.key.stop()
        self.engine.dispose()

    def _admin(self, action: str, room: str = ROOM["number"], key: str | None = KEY, **params):
        headers = {"X-Admin-Key": key} if key else {}
        return self.client.post(
            f"/api/v1/admin/hotels/{DEMO_HOTEL_SLUG}/rooms/{room}/{action}", params=params, headers=headers
        )

    def _connect(self):
        return self.client.post("/api/v1/stays/connect", json={"connect_code": ROOM["code"]})

    def _verify(self, connection_id: str, pin: str):
        return self.client.post("/api/v1/stays/verify", json={"connection_id": connection_id, "pin": pin})

    def _connect_and_verify(self, pin: str):
        return self._verify(self._connect().json()["connection_id"], pin)

    def test_an_issued_stay_pin_replaces_the_room_pin(self):
        issued = self._admin("stay-pin")
        self.assertEqual(issued.status_code, 200, issued.text)
        pin = issued.json()["pin"]
        self.assertRegex(pin, r"^\d{6}$")
        self.assertEqual(self._connect_and_verify(ROOM["pin"]).status_code, 401)
        self.assertEqual(self._connect_and_verify(pin).status_code, 200)

    def test_only_the_hash_of_the_stay_pin_is_stored(self):
        pin = self._admin("stay-pin").json()["pin"]
        db = self.Session()
        try:
            stored = db.get(Stay, self._admin_stay_id(pin)).verification_pin_hash
        finally:
            db.close()
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn(pin, stored)

    def _admin_stay_id(self, pin: str) -> str:
        return self._connect_and_verify(pin).json()["stay"]["id"]

    def test_revoke_devices_signs_out_devices_already_connected(self):
        token = self._connect_and_verify(ROOM["pin"]).json()["access_token"]
        issued = self._admin("stay-pin", revoke_devices="true").json()
        self.assertEqual(issued["revoked_devices"], 1)
        active = self.client.get("/api/v1/stays/active", headers={"X-Stay-Token": token}).json()
        self.assertFalse(active["active"])

    def test_unlock_lifts_the_lockout_and_closes_open_challenges(self):
        stale = self._connect().json()["connection_id"]
        for _ in range(5):
            self.assertEqual(self._verify(stale, WRONG_PIN).status_code, 401)
        self.assertEqual(self._connect().status_code, 429)

        unlocked = self._admin("pin-unlock")
        self.assertEqual(unlocked.status_code, 200, unlocked.text)
        self.assertEqual(unlocked.json()["closed_challenges"], 1)
        # The challenge opened before the unlock cannot be used to keep guessing.
        self.assertEqual(self._verify(stale, ROOM["pin"]).status_code, 410)
        self.assertEqual(self._connect_and_verify(ROOM["pin"]).status_code, 200)

    def test_wrong_pins_after_an_unlock_count_again(self):
        first = self._connect().json()["connection_id"]
        for _ in range(5):
            self._verify(first, WRONG_PIN)
        self._admin("pin-unlock")
        second = self._connect().json()["connection_id"]
        for _ in range(5):
            self.assertEqual(self._verify(second, WRONG_PIN).status_code, 401)
        self.assertEqual(self._connect().status_code, 429)

    def test_both_calls_need_the_operator_key(self):
        for action in ("stay-pin", "pin-unlock"):
            with self.subTest(action=action):
                self.assertEqual(self._admin(action, key=None).status_code, 401)
                self.assertEqual(self._admin(action, key="wrong").status_code, 401)
                with patch.object(settings, "admin_api_key", ""):
                    self.assertEqual(self._admin(action).status_code, 404)

    def test_unknown_rooms_are_not_found(self):
        for action in ("stay-pin", "pin-unlock"):
            with self.subTest(action=action):
                self.assertEqual(self._admin(action, room="999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
