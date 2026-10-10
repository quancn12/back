"""C1: price questions get an honest fixed answer instead of a model guess.

No official tour price list exists (owner + effective date), so the Travel
answer must never contain an invented figure. The promises: price questions are
recognised in Vietnamese (with or without accents), English and Chinese; they
are answered without calling the model; itinerary requests that mention cost,
cancellation-fee questions (C2) and ordinary questions are left alone.
"""
from __future__ import annotations

import asyncio
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.api import unified as unified_api
from app.api.travel_chat import _deterministic_reply, travel_chat
from app.core.database import Base, get_db
from app.models import auth, destination, hotel, knowledge, unified  # noqa: F401
from app.models.schemas import TravelChatRequest
from app.services.travel_price import is_price_query, price_reply

PRICE_QUESTIONS = [
    "Tour Hồ Thác Bà giá bao nhiêu?",
    "giá tour HVNT 0006-24",
    "Giá vé tham quan đảo Hoa là bao nhiêu",
    "tour nay gia khoang bao nhieu",
    "Chi phí đi Suối Giàng 2 ngày?",
    "đi 2 ngày hết bao nhiêu tiền",
    "Cho mình bảng giá",
    "How much is the Thac Ba tour?",
    "What is the price of HVTB 0004-23?",
    "这个行程多少钱？",
    "团费是多少",
]
NOT_PRICE_QUESTIONS = [
    "Gia đình có bao nhiêu người thì nên đi tour nào?",
    "Hồ Thác Bà có giá trị lịch sử gì?",
    "Đi Hồ Thác Bà mấy ngày là hợp lý?",
    "How much time do I need in Sa Pa?",
    "Lịch trình 2 ngày Hồ Thác Bà",
]


class DetectionTests(unittest.TestCase):
    def test_price_questions_are_recognised(self):
        for question in PRICE_QUESTIONS:
            with self.subTest(question=question):
                self.assertTrue(is_price_query(question))

    def test_other_questions_are_not(self):
        for question in NOT_PRICE_QUESTIONS:
            with self.subTest(question=question):
                self.assertFalse(is_price_query(question))

    def test_the_answer_never_contains_a_figure(self):
        for locale in ("vi", "en", "zh-Hans"):
            with self.subTest(locale=locale):
                self.assertIsNone(re.search(r"\d", price_reply(locale)))


class LegacyTravelRouteTests(unittest.TestCase):
    def test_price_question_is_answered_without_the_model(self):
        session = SimpleNamespace(session_id="session-1", add_turn=MagicMock())
        payload = TravelChatRequest(message="Tour Hồ Thác Bà giá bao nhiêu?")
        prepared = (session, [], [], "VN", None, payload.message)
        with patch("app.api.travel_chat._prepare_chat", new=AsyncMock(return_value=prepared)), \
             patch("app.api.travel_chat.llm_client.chat", new=AsyncMock()) as llm_chat:
            response = asyncio.run(travel_chat(payload, MagicMock()))
        self.assertEqual(response.reply, price_reply("vi"))
        llm_chat.assert_not_awaited()

    def test_cancellation_fee_stays_with_the_policy_answer(self):
        reply = _deterministic_reply("Phí hủy tour HVTB 0004-23 là bao nhiêu?", [])
        self.assertNotEqual(reply, price_reply("vi"))
        self.assertIsNotNone(reply)

    def test_ordinary_and_itinerary_questions_go_to_the_model(self):
        for question in ("Hồ Thác Bà có gì đặc biệt?", "Lên lịch trình 2 ngày Hồ Thác Bà, chi phí tiết kiệm"):
            with self.subTest(question=question):
                self.assertIsNone(_deterministic_reply(question, []))


class UnifiedStreamTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(auth_api.router)
        app.include_router(unified_api.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.client.headers["X-CSRF-Token"] = self.client.get("/api/v1/auth/session").json()["csrf_token"]

    def tearDown(self):
        self.engine.dispose()

    def _ask(self, message: str, locale: str = "vi") -> tuple[str, MagicMock]:
        conversation = self.client.post("/api/v1/conversations", json={"locale": locale}).json()["id"]
        model = MagicMock(side_effect=AssertionError("the model must not be called"))
        with patch("app.core.database.SessionLocal", self.Session), patch(
            "app.services.unified_chat.travel_vector_store.query", new=AsyncMock(return_value=[])
        ), patch("app.services.unified_chat.llm_client.chat_stream", new=model), patch(
            "app.services.unified_chat._try_build_itinerary_artifact_plan", new=AsyncMock(return_value=None)
        ):
            text = self.client.post(
                f"/api/v1/conversations/{conversation}/messages/stream",
                json={"message": message, "mode": "TRAVEL", "locale": locale},
            ).text
        return text, model

    def test_a_price_question_gets_the_honest_answer_in_each_language(self):
        for locale, question, marker in (
            ("vi", "Tour Hồ Thác Bà giá bao nhiêu?", "bảng giá tour chính thức"),
            ("en", "How much is the Thac Ba tour?", "official tour price list"),
            ("zh-Hans", "这个行程多少钱？", "正式的行程价格表"),
        ):
            with self.subTest(locale=locale):
                text, model = self._ask(question, locale)
                self.assertIn(marker, text)
                model.assert_not_called()

    def test_an_itinerary_that_mentions_cost_is_not_turned_into_a_price_answer(self):
        text, _ = self._ask("Lên lịch trình 2 ngày 1 đêm Hồ Thác Bà, chi phí tiết kiệm")
        self.assertNotIn("bảng giá tour chính thức", text)


if __name__ == "__main__":
    unittest.main()
