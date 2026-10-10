"""Partner chat API (option D): keys, isolation, both answer shapes, limits.

A partner website builds its own chat UI and calls from its server with a
secret key and its own user id. These tests pin who can reach what, that the
answer is the PWA's answer, and that no cookie or CSRF token is involved.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.api import partner as partner_api
from app.api import unified as unified_api
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import auth as auth_models  # noqa: F401
from app.models import destination, hotel, knowledge  # noqa: F401
from app.models.partner import Partner
from app.models.unified import UnifiedConversation, UnifiedMessage
from app.services.partner_access import partner_rate_limiter

ADMIN = "partner-test-admin-key"


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        out.append((lines.get("event"), json.loads(lines.get("data", "{}"))))
    return out


async def fake_model(*_args, **_kwargs):
    for part in ("Hồ Thác Bà ", "có đi thuyền ", "và hang động."):
        yield part


async def broken_model(*_args, **_kwargs):
    yield "Hồ "
    raise RuntimeError("secret internal detail /srv/db")


class PartnerApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        app = FastAPI()
        for router in (auth_api.router, unified_api.router, partner_api.router):
            app.include_router(router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.app = app
        self.client = TestClient(app)
        partner_rate_limiter.reset()
        self.patches = [
            patch.object(settings, "admin_api_key", ADMIN),
            patch("app.core.database.SessionLocal", self.Session),
            patch("app.services.unified_chat.travel_vector_store.query", new=AsyncMock(return_value=[])),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def _create_partner(self, slug="tour-site", **extra):
        response = self.client.post("/api/v1/admin/partners", json={"slug": slug, "name": slug, **extra},
                                    headers={"X-Admin-Key": ADMIN})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _partner_client(self, key, user="user-1"):
        client = TestClient(self.app)
        client.headers.update({"Authorization": f"Bearer {key}", "X-Partner-User": user})
        return client

    def _conversation(self, client, **body):
        response = client.post("/api/v1/partner/conversations", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_keys_are_issued_by_the_operator_and_stored_only_as_a_digest(self):
        self.assertEqual(self.client.post("/api/v1/admin/partners", json={"slug": "x1", "name": "x"}).status_code, 401)
        created = self._create_partner()
        key = created["api_key"]
        self.assertRegex(key, r"^vsk_[0-9a-f]{8}_[A-Za-z0-9_-]{32,}$")
        self.assertTrue(key.startswith(created["key_prefix"] + "_"))
        db = self.Session()
        row = db.scalar(select(Partner))
        self.assertNotIn(key, (row.key_hash, row.key_prefix))
        self.assertEqual(len(row.key_hash), 64)
        db.close()
        self.assertEqual(self.client.post("/api/v1/admin/partners", json={"slug": "tour-site", "name": "again"},
                                          headers={"X-Admin-Key": ADMIN}).status_code, 409)
        listed = self.client.get("/api/v1/admin/partners", headers={"X-Admin-Key": ADMIN}).json()
        self.assertEqual([p["slug"] for p in listed], ["tour-site"])
        self.assertNotIn("api_key", listed[0])

    def test_a_request_needs_a_valid_active_key_and_a_user_id(self):
        key = self._create_partner()["api_key"]
        bare = TestClient(self.app)
        missing = bare.post("/api/v1/partner/conversations", json={}, headers={"X-Partner-User": "u"})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.headers["www-authenticate"], "Bearer")
        for wrong in (key[:-1] + ("A" if key[-1] != "A" else "B"), "vsk_00000000_" + "a" * 43, "not-a-key", key.replace("vsk_", "vsx_")):
            with self.subTest(wrong=wrong):
                self.assertEqual(self._partner_client(wrong).post("/api/v1/partner/conversations", json={}).status_code, 401)
        no_user = TestClient(self.app)
        no_user.headers["Authorization"] = f"Bearer {key}"
        self.assertEqual(no_user.post("/api/v1/partner/conversations", json={}).status_code, 400)
        self.assertEqual(self._partner_client(key).post("/api/v1/partner/conversations", json={}).status_code, 201)

    def test_rotation_and_deactivation_take_effect_at_once(self):
        old = self._create_partner()["api_key"]
        conversation = self._conversation(self._partner_client(old))
        new = self.client.post("/api/v1/admin/partners/tour-site/rotate-key", headers={"X-Admin-Key": ADMIN}).json()["api_key"]
        self.assertEqual(self._partner_client(old).get(f"/api/v1/partner/conversations/{conversation}/messages").status_code, 401)
        self.assertEqual(self._partner_client(new).get(f"/api/v1/partner/conversations/{conversation}/messages").status_code, 200,
                         "conversations stay with the partner across a rotation")
        self.client.post("/api/v1/admin/partners/tour-site/deactivate", headers={"X-Admin-Key": ADMIN})
        self.assertEqual(self._partner_client(new).post("/api/v1/partner/conversations", json={}).status_code, 401)

    def test_conversations_are_isolated_per_partner_per_user_and_from_the_pwa(self):
        key_a = self._create_partner("site-a")["api_key"]
        key_b = self._create_partner("site-b")["api_key"]
        conversation = self._conversation(self._partner_client(key_a, "u1"))
        path = f"/api/v1/partner/conversations/{conversation}/messages"
        self.assertEqual(self._partner_client(key_a, "u1").get(path).status_code, 200)
        self.assertEqual(self._partner_client(key_a, "u2").get(path).status_code, 404)
        self.assertEqual(self._partner_client(key_b, "u1").get(path).status_code, 404)
        # The PWA's guest or account session cannot open a partner conversation...
        self.assertEqual(self.client.get(f"/api/v1/conversations/{conversation}").status_code, 404)
        # ...and a partner cannot open a PWA one.
        self.client.headers["X-CSRF-Token"] = self.client.get("/api/v1/auth/session").json()["csrf_token"]
        pwa = self.client.post("/api/v1/conversations", json={"locale": "vi"}).json()["id"]
        self.assertEqual(self._partner_client(key_a, "u1").get(f"/api/v1/partner/conversations/{pwa}/messages").status_code, 404)
        db = self.Session()
        row = db.get(UnifiedConversation, conversation)
        self.assertIsNone(row.owner_session_id)
        self.assertIsNone(row.owner_user_id)
        self.assertEqual(len(row.owner_partner_user), 64)
        self.assertNotEqual(row.owner_partner_user, "u1", "the partner's raw user id is not stored")
        db.close()

    def test_the_stream_is_the_pwa_answer_without_cookies_or_csrf(self):
        client = self._partner_client(self._create_partner()["api_key"])
        conversation = self._conversation(client, locale="vi")
        model = MagicMock(side_effect=AssertionError("a greeting does not call the model"))
        with patch("app.api.unified.llm_client.chat_stream", new=model):
            response = client.post(f"/api/v1/partner/conversations/{conversation}/messages/stream", json={"message": "xin chào"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertNotIn("set-cookie", response.headers)
        parsed = events(response.text)
        self.assertEqual(parsed[0][0], "replace")
        self.assertIn("Xin chào", parsed[0][1]["text"])
        kind, done = parsed[-1]
        self.assertEqual(kind, "done")
        self.assertEqual(done["conversation_id"], conversation)
        self.assertIsInstance(done["message_id"], int)

    def test_the_json_endpoint_returns_the_whole_turn_and_history_keeps_it(self):
        client = self._partner_client(self._create_partner()["api_key"])
        conversation = self._conversation(client)
        with patch("app.api.unified.llm_client.chat_stream", new=fake_model):
            response = client.post(f"/api/v1/partner/conversations/{conversation}/messages", json={"message": "Hồ Thác Bà có gì chơi?"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["reply"], "Hồ Thác Bà có đi thuyền và hang động.")
        self.assertEqual(body["conversation_id"], conversation)
        self.assertIsInstance(body["message_id"], int)
        history = client.get(f"/api/v1/partner/conversations/{conversation}/messages").json()
        self.assertEqual([(m["role"], m["content"]) for m in history],
                         [("user", "Hồ Thác Bà có gì chơi?"), ("assistant", "Hồ Thác Bà có đi thuyền và hang động.")])

    def test_only_travel_turns_are_accepted(self):
        client = self._partner_client(self._create_partner()["api_key"])
        conversation = self._conversation(client)
        response = client.post(f"/api/v1/partner/conversations/{conversation}/messages", json={"message": "gọi dọn phòng", "mode": "HOTEL"})
        self.assertEqual(response.status_code, 422, "hotel services need a verified stay, which only the PWA sets up")

    def test_a_failure_mid_answer_does_not_leak_the_error(self):
        client = self._partner_client(self._create_partner()["api_key"])
        conversation = self._conversation(client)
        with patch("app.api.unified.llm_client.chat_stream", new=broken_model),                 self.assertLogs("app.api.unified", level="WARNING") as logged:
            stream = client.post(f"/api/v1/partner/conversations/{conversation}/messages/stream", json={"message": "Hồ Thác Bà?"}).text
            answer = client.post(f"/api/v1/partner/conversations/{conversation}/messages", json={"message": "Hồ Thác Bà?"})
        self.assertEqual(events(stream)[-1], ("error", {"detail": "Không thể hoàn tất câu trả lời."}))
        self.assertEqual(answer.status_code, 502)
        for text in (stream, answer.text):
            self.assertNotIn("secret internal detail", text)
        self.assertIn("secret internal detail", " ".join(logged.output), "the operator still sees it in the server log")

    def test_each_partner_has_its_own_rate_limit(self):
        limited = self._partner_client(self._create_partner("small", rate_limit_per_minute=3)["api_key"])
        other = self._partner_client(self._create_partner("big")["api_key"])
        for _ in range(3):
            self.assertEqual(limited.post("/api/v1/partner/conversations", json={}).status_code, 201)
        blocked = limited.post("/api/v1/partner/conversations", json={})
        self.assertEqual(blocked.status_code, 429)
        self.assertGreaterEqual(int(blocked.headers["retry-after"]), 1)
        self.assertEqual(other.post("/api/v1/partner/conversations", json={}).status_code, 201)

    def test_deleting_a_conversation_erases_its_messages(self):
        client = self._partner_client(self._create_partner()["api_key"])
        conversation = self._conversation(client)
        with patch("app.api.unified.llm_client.chat_stream", new=fake_model):
            client.post(f"/api/v1/partner/conversations/{conversation}/messages", json={"message": "Hồ Thác Bà?"})
        self.assertEqual(self._partner_client(client.headers["Authorization"][7:], "someone-else")
                         .delete(f"/api/v1/partner/conversations/{conversation}").status_code, 404)
        self.assertEqual(client.delete(f"/api/v1/partner/conversations/{conversation}").status_code, 204)
        self.assertEqual(client.get(f"/api/v1/partner/conversations/{conversation}/messages").status_code, 404)
        db = self.Session()
        self.assertEqual(db.scalar(select(func.count()).select_from(UnifiedMessage)), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
