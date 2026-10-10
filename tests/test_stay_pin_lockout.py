"""The five-try PIN limit must hold across challenges, not just within one.

A challenge is free to create: anyone holding a room's connect code -- printed
on a QR in the room -- gets a fresh one per scan. The counter used to live on
the challenge, so rescanning reset it and the PIN could be guessed without
limit. Measured before the fix: four rescans let the server grade 20 guesses.
"""
from __future__ import annotations

import unittest
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth, unified
from app.core.database import Base, get_db
from app.models import auth as auth_models  # noqa: F401
from app.models import destination, hotel, knowledge  # noqa: F401
from app.models.unified import StayConnectionChallenge, utcnow
from app.services.demo_seed import seed_unified_demo

ROOM_305 = {"code": "demo-an-nhien-305", "pin": "3056", "id": "demo-room-305"}
ROOM_410 = {"code": "demo-an-nhien-410", "pin": "4106"}
WRONG_PIN = "0000"


class StayPinLockoutTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
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
        session = self.client.get("/api/v1/auth/session")
        self.client.headers.update({"X-CSRF-Token": session.json()["csrf_token"]})

    def tearDown(self):
        self.engine.dispose()

    def _connect(self, room=ROOM_305):
        return self.client.post("/api/v1/stays/connect", json={"connect_code": room["code"]})

    def _verify(self, connection_id, pin):
        return self.client.post(
            "/api/v1/stays/verify",
            json={"connection_id": connection_id, "pin": pin},
        )

    def _fail(self, times, room=ROOM_305):
        connection_id = self._connect(room).json()["connection_id"]
        for _ in range(times):
            self.assertEqual(self._verify(connection_id, WRONG_PIN).status_code, 401)
        return connection_id

    def _age_challenges(self, by: timedelta, room_id=ROOM_305["id"]):
        db = self.Session()
        try:
            for challenge in db.query(StayConnectionChallenge).filter(
                StayConnectionChallenge.room_id == room_id
            ):
                challenge.created_at = challenge.created_at - by
            db.commit()
        finally:
            db.close()

    def test_a_guest_who_mistypes_a_few_times_still_gets_in(self):
        connection_id = self._fail(4)
        self.assertEqual(self._verify(connection_id, ROOM_305["pin"]).status_code, 200)

    def test_rescanning_the_code_no_longer_resets_the_count(self):
        self._fail(5)
        # The old counter lived on the challenge; this new scan used to start
        # again from zero.
        response = self._connect()
        self.assertEqual(response.status_code, 429)
        self.assertIn("lễ tân", response.json()["detail"])

    def test_the_server_grades_at_most_five_guesses_however_many_scans(self):
        graded = 0
        for _ in range(4):
            connected = self._connect()
            if connected.status_code != 200:
                continue
            for _ in range(5):
                graded += self._verify(connected.json()["connection_id"], WRONG_PIN).status_code == 401
        self.assertEqual(graded, 5)

    def test_even_the_right_pin_is_refused_while_the_room_is_locked(self):
        # Otherwise the lock would only slow down wrong answers, and the sixth
        # guess being correct would still let an attacker in.
        connection_id = self._fail(4)
        other = self._connect().json()["connection_id"]
        self.assertEqual(self._verify(connection_id, WRONG_PIN).status_code, 401)
        self.assertEqual(self._verify(other, ROOM_305["pin"]).status_code, 429)

    def test_the_short_lock_clears_on_its_own(self):
        self._fail(5)
        self.assertEqual(self._connect().status_code, 429)
        self._age_challenges(timedelta(minutes=16))
        connected = self._connect()
        self.assertEqual(connected.status_code, 200)
        self.assertEqual(
            self._verify(connected.json()["connection_id"], ROOM_305["pin"]).status_code,
            200,
        )

    def test_the_daily_cap_stops_riding_the_short_window(self):
        # Four rounds of four misses, each aged past the short window, stay
        # under the 15-minute rule but add up to 16 in the day.
        for _ in range(4):
            self._fail(4)
            self._age_challenges(timedelta(minutes=16))
        # Four more reach 20 for the day.
        self._fail(4)
        self._age_challenges(timedelta(minutes=16))
        self.assertEqual(self._connect().status_code, 429)
        # A day later the room opens again.
        self._age_challenges(timedelta(hours=24))
        self.assertEqual(self._connect().status_code, 200)

    def test_locking_one_room_leaves_the_others_alone(self):
        self._fail(5)
        self.assertEqual(self._connect().status_code, 429)
        connected = self._connect(ROOM_410)
        self.assertEqual(connected.status_code, 200)
        self.assertEqual(
            self._verify(connected.json()["connection_id"], ROOM_410["pin"]).status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
