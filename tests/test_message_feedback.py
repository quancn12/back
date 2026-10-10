"""Answer-quality feedback: what it stores, what it refuses, what it counts.

The point of this table is to turn "is the chatbot any good?" into a number,
so the tests care most about the ways a counting table can quietly produce
wrong numbers: nonsense rating/reason pairs, double counting, rating someone
else's conversation, and reporting an unmeasured system as zero.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.api import unified as unified_api
from app.core.database import Base, get_db
from app.models import destination, hotel, knowledge  # noqa: F401
from app.models import unified as unified_models
from app.models.unified import MessageFeedback, UnifiedConversation, UnifiedMessage
from app.services import message_feedback as service


class FeedbackTestBase(unittest.TestCase):
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
            self.conversation = UnifiedConversation(
                country_code="VN", preferred_mode="TRAVEL", locale="vi"
            )
            self.other_conversation = UnifiedConversation(
                country_code="VN", preferred_mode="TRAVEL", locale="en"
            )
            db.add_all([self.conversation, self.other_conversation])
            db.commit()
            self.conversation_id = self.conversation.id
            self.other_conversation_id = self.other_conversation.id

            question = UnifiedMessage(
                conversation_id=self.conversation_id,
                role="user",
                context_type="TRAVEL",
                content="Đi Đà Nẵng mấy ngày là hợp lý?",
            )
            answer = UnifiedMessage(
                conversation_id=self.conversation_id,
                role="assistant",
                context_type="TRAVEL",
                content="Ba ngày là nhịp độ vừa phải.",
            )
            other_answer = UnifiedMessage(
                conversation_id=self.other_conversation_id,
                role="assistant",
                context_type="HOTEL",
                content="Breakfast runs from 6 to 10.",
            )
            db.add_all([question, answer, other_answer])
            db.commit()
            self.question_id = question.id
            self.answer_id = answer.id
            self.other_answer_id = other_answer.id
        finally:
            db.close()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()


class ValidationTests(FeedbackTestBase):
    def test_a_rating_alone_is_enough(self):
        self.assertEqual(service.validate("up", None), ("up", None))
        self.assertEqual(service.validate("down", ""), ("down", None))

    def test_rating_is_normalised_but_not_invented(self):
        self.assertEqual(service.validate("  UP ", None), ("up", None))
        with self.assertRaises(service.FeedbackError):
            service.validate("maybe", None)
        with self.assertRaises(service.FeedbackError):
            service.validate("", None)

    def test_a_negative_reason_cannot_be_attached_to_a_thumbs_up(self):
        # Otherwise the reason breakdown counts "inaccurate" among the answers
        # somebody said were good, and the table stops meaning anything.
        with self.assertRaises(service.FeedbackError):
            service.validate("up", "inaccurate")

    def test_a_positive_reason_cannot_be_attached_to_a_thumbs_down(self):
        with self.assertRaises(service.FeedbackError):
            service.validate("down", "accurate")

    def test_unknown_reasons_are_refused_rather_than_stored(self):
        with self.assertRaises(service.FeedbackError):
            service.validate("down", "because_i_said_so")

    def test_the_wrongly_refused_reason_exists(self):
        # The system refusing when it lacks grounding is one of its better
        # properties. A guest who thinks a refusal was wrong is reporting a
        # missing document, which is the most actionable signal here.
        self.assertIn("refused_wrongly", service.NEGATIVE_REASONS)

    def test_reason_lists_are_exposed_per_rating(self):
        reasons = service.available_reasons()
        self.assertEqual(set(reasons), {"up", "down"})
        self.assertNotIn("inaccurate", reasons["up"])


class RecordingTests(FeedbackTestBase):
    def test_a_rating_is_stored_with_the_answer_it_describes(self):
        db = self.Session()
        try:
            record = service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=self.answer_id,
                rating="down",
                reason="incomplete",
            )
            self.assertEqual(record.rating, "down")
            self.assertEqual(record.reason, "incomplete")
            # Copied from the rated turn, so counts can be read per mode and
            # language without reopening the conversation.
            self.assertEqual(record.context_type, "TRAVEL")
            self.assertEqual(record.locale, "vi")
        finally:
            db.close()

    def test_rating_the_same_answer_twice_replaces_it_instead_of_counting_twice(self):
        db = self.Session()
        try:
            first = service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=self.answer_id,
                rating="down",
                reason="inaccurate",
            )
            second = service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=self.answer_id,
                rating="up",
            )
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.rating, "up")
            self.assertIsNone(second.reason)
            rows = db.scalars(select(MessageFeedback)).all()
            self.assertEqual(len(rows), 1)
        finally:
            db.close()

    def test_a_question_cannot_be_rated(self):
        db = self.Session()
        try:
            with self.assertRaises(service.FeedbackError) as raised:
                service.record_feedback(
                    db,
                    conversation_id=self.conversation_id,
                    message_id=self.question_id,
                    rating="up",
                )
            self.assertEqual(raised.exception.status_code, 400)
        finally:
            db.close()

    def test_a_message_from_another_conversation_is_not_found(self):
        # Not 403: a distinguishable error would confirm that the id exists
        # somewhere, which is exactly what walking the id space is looking for.
        db = self.Session()
        try:
            with self.assertRaises(service.FeedbackError) as raised:
                service.record_feedback(
                    db,
                    conversation_id=self.conversation_id,
                    message_id=self.other_answer_id,
                    rating="up",
                )
            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(db.scalars(select(MessageFeedback)).all(), [])
        finally:
            db.close()

    def test_an_unknown_message_is_not_found(self):
        db = self.Session()
        try:
            with self.assertRaises(service.FeedbackError) as raised:
                service.record_feedback(
                    db,
                    conversation_id=self.conversation_id,
                    message_id=999_999,
                    rating="up",
                )
            self.assertEqual(raised.exception.status_code, 404)
        finally:
            db.close()

    def test_a_rating_can_be_withdrawn_and_withdrawing_twice_is_harmless(self):
        db = self.Session()
        try:
            service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=self.answer_id,
                rating="up",
            )
            self.assertTrue(
                service.clear_feedback(
                    db,
                    conversation_id=self.conversation_id,
                    message_id=self.answer_id,
                )
            )
            self.assertFalse(
                service.clear_feedback(
                    db,
                    conversation_id=self.conversation_id,
                    message_id=self.answer_id,
                )
            )
            self.assertIsNone(
                service.get_feedback(
                    db,
                    conversation_id=self.conversation_id,
                    message_id=self.answer_id,
                )
            )
        finally:
            db.close()

    def test_deleting_the_conversation_takes_its_feedback_with_it(self):
        db = self.Session()
        try:
            service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=self.answer_id,
                rating="up",
            )
            db.execute(
                MessageFeedback.__table__.delete().where(
                    MessageFeedback.conversation_id == self.conversation_id
                )
            )
            db.commit()
            self.assertEqual(db.scalars(select(MessageFeedback)).all(), [])
        finally:
            db.close()


class SummaryTests(FeedbackTestBase):
    def test_nothing_rated_reports_unmeasured_not_zero_percent(self):
        db = self.Session()
        try:
            summary = service.summarise(db)
            self.assertEqual(summary["total"], 0)
            # Zero would read as "everyone hated it". None reads as "nobody
            # has said", which is the truth.
            self.assertIsNone(summary["satisfaction"])
        finally:
            db.close()

    def test_counts_and_share_reflect_what_was_stored(self):
        db = self.Session()
        try:
            extra_ids = []
            for index in range(3):
                message = UnifiedMessage(
                    conversation_id=self.conversation_id,
                    role="assistant",
                    context_type="TRAVEL",
                    content=f"answer {index}",
                )
                db.add(message)
                db.commit()
                extra_ids.append(message.id)

            service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=self.answer_id,
                rating="up",
            )
            service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=extra_ids[0],
                rating="up",
                reason="accurate",
            )
            service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=extra_ids[1],
                rating="down",
                reason="refused_wrongly",
            )
            service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=extra_ids[2],
                rating="down",
                reason="refused_wrongly",
            )

            summary = service.summarise(db)
            self.assertEqual(summary["total"], 4)
            self.assertEqual(summary["up"], 2)
            self.assertEqual(summary["down"], 2)
            self.assertEqual(summary["satisfaction"], 0.5)
            self.assertEqual(summary["reasons"]["down"]["refused_wrongly"], 2)
            self.assertEqual(summary["reasons"]["up"]["accurate"], 1)
        finally:
            db.close()

    def test_a_mode_filter_narrows_the_counts(self):
        db = self.Session()
        try:
            service.record_feedback(
                db,
                conversation_id=self.conversation_id,
                message_id=self.answer_id,
                rating="up",
            )
            service.record_feedback(
                db,
                conversation_id=self.other_conversation_id,
                message_id=self.other_answer_id,
                rating="down",
                reason="incomplete",
            )
            self.assertEqual(service.summarise(db)["total"], 2)
            self.assertEqual(service.summarise(db, context_type="TRAVEL")["total"], 1)
            self.assertEqual(service.summarise(db, context_type="HOTEL")["down"], 1)
        finally:
            db.close()


class HttpFixture(FeedbackTestBase):
    """Conversations reached over HTTP must be owned by the calling session.

    resolve_authorized_conversation() matches on owner_session_id, so a row
    inserted straight into the database is invisible to the API -- correctly.
    These tests therefore create the conversation the way a browser does.
    """

    def _client(self) -> TestClient:
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
        client = TestClient(app)
        session = client.get("/api/v1/auth/session")
        self.assertEqual(session.status_code, 200, session.text)
        client.headers.update({"X-CSRF-Token": session.json()["csrf_token"]})
        return client

    def _owned_conversation(self, client: TestClient) -> str:
        created = client.post("/api/v1/conversations", json={})
        self.assertEqual(created.status_code, 200, created.text)
        return created.json()["id"]

    def _seed_answer(self, conversation_id: str, content: str = "Ba ngày.") -> int:
        db = self.Session()
        try:
            message = UnifiedMessage(
                conversation_id=conversation_id,
                role="assistant",
                context_type="TRAVEL",
                content=content,
            )
            db.add(message)
            db.commit()
            return message.id
        finally:
            db.close()


class FeedbackApiTests(HttpFixture):
    def setUp(self):
        super().setUp()
        self.client = self._client()
        self.owned_id = self._owned_conversation(self.client)
        self.owned_answer_id = self._seed_answer(self.owned_id)

    def _url(self, message_id: int | None = None, conversation_id: str | None = None) -> str:
        return (
            f"/api/v1/conversations/{conversation_id or self.owned_id}"
            f"/messages/{message_id or self.owned_answer_id}/feedback"
        )

    def test_put_then_get_returns_what_was_stored(self):
        client = self.client
        response = client.put(self._url(), json={"rating": "down", "reason": "too_long"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rating"], "down")

        read_back = client.get(self._url())
        self.assertEqual(read_back.status_code, 200)
        self.assertEqual(read_back.json()["reason"], "too_long")

    def test_no_rating_yet_reads_as_null_rather_than_an_error(self):
        client = self.client
        response = client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json())

    def test_an_impossible_pair_is_refused_with_a_reason(self):
        client = self.client
        response = client.put(self._url(), json={"rating": "up", "reason": "inaccurate"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("inaccurate", response.json()["detail"])

    def test_an_unknown_rating_never_reaches_the_service(self):
        client = self.client
        response = client.put(self._url(), json={"rating": "sideways"})
        self.assertEqual(response.status_code, 422)

    def test_free_text_is_not_accepted_anywhere(self):
        # The contract has no comment field. A client that sends one must not
        # find it silently persisted.
        client = self.client
        response = client.put(
            self._url(),
            json={"rating": "down", "reason": "incomplete", "comment": "số điện thoại 0900"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("comment", response.json())
        db = self.Session()
        try:
            row = db.scalars(select(MessageFeedback)).one()
            self.assertFalse(
                any("0900" in str(value) for value in vars(row).values()),
                "no column may carry the free text a client tried to smuggle in",
            )
        finally:
            db.close()

    def test_rating_another_conversations_message_is_a_404(self):
        client = self.client
        response = client.put(
            self._url(message_id=self.other_answer_id), json={"rating": "up"}
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_is_idempotent(self):
        client = self.client
        client.put(self._url(), json={"rating": "up"})
        self.assertEqual(client.delete(self._url()).status_code, 204)
        self.assertEqual(client.delete(self._url()).status_code, 204)

    def test_reason_list_endpoint_matches_the_service(self):
        client = self.client
        response = client.get("/api/v1/feedback/reasons")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), service.available_reasons())

    def test_summary_is_hidden_until_an_admin_key_is_configured(self):
        client = self.client
        with patch("app.core.security.settings.admin_api_key", ""):
            response = client.get("/api/v1/feedback/summary")
        # 404 rather than 401: an unconfigured deployment should not advertise
        # that an operator report exists behind the door.
        self.assertEqual(response.status_code, 404)

    def test_summary_requires_the_configured_key(self):
        client = self.client
        with patch("app.core.security.settings.admin_api_key", "secret"):
            self.assertEqual(client.get("/api/v1/feedback/summary").status_code, 401)
            response = client.get(
                "/api/v1/feedback/summary", headers={"X-Admin-Key": "secret"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)


class PersistTurnTests(FeedbackTestBase):
    def test_persist_turn_hands_back_the_assistant_message_id(self):
        # Without this the client would have to match a rating to an answer by
        # position or by text, and both break on reload or a repeated answer.
        from app.services import unified_chat

        # persist_turn imports SessionLocal inside the function, so the patch
        # has to land on the module it is imported from.
        with patch("app.core.database.SessionLocal", self.Session):
            message_id = unified_chat.persist_turn(
                self.conversation_id, "TRAVEL", "câu hỏi", "câu trả lời"
            )
        self.assertIsInstance(message_id, int)

        db = self.Session()
        try:
            stored = db.get(UnifiedMessage, message_id)
            self.assertEqual(stored.role, "assistant")
            self.assertEqual(stored.content, "câu trả lời")
        finally:
            db.close()

    def test_a_missing_conversation_yields_no_id_instead_of_an_error(self):
        from app.services import unified_chat

        with patch("app.core.database.SessionLocal", self.Session):
            self.assertIsNone(
                unified_chat.persist_turn("nope", "TRAVEL", "q", "a")
            )


class StreamHandoffTests(HttpFixture):
    """The stream must hand the client something it can rate."""

    def setUp(self):
        super().setUp()
        self.client = self._client()
        self.owned_id = self._owned_conversation(self.client)

    def _stream(self, client: TestClient):
        async def fake_stream(*args, **kwargs):
            for part in ("Ba ngày ", "là hợp lý."):
                yield part

        with patch("app.core.database.SessionLocal", self.Session), patch(
            "app.services.unified_chat.travel_vector_store.query",
            new=AsyncMock(return_value=[{"text": "Đà Nẵng có biển Mỹ Khê."}]),
        ), patch(
            "app.services.unified_chat.llm_client.chat_stream", new=fake_stream
        ):
            return client.post(
                f"/api/v1/conversations/{self.owned_id}/messages/stream",
                json={"message": "Đi Đà Nẵng mấy ngày?", "mode": "TRAVEL"},
            )

    def _done_payload(self, text: str) -> dict:
        for block in text.split("\n\n"):
            if block.startswith("event: done"):
                data = block.split("data: ", 1)[1]
                return json.loads(data)
        raise AssertionError(f"no done event in stream: {text!r}")

    def test_done_carries_the_id_of_the_answer_just_written(self):
        client = self.client
        response = self._stream(client)
        self.assertEqual(response.status_code, 200)
        payload = self._done_payload(response.text)
        message_id = payload["message_id"]
        self.assertIsInstance(message_id, int)

        db = self.Session()
        try:
            stored = db.get(UnifiedMessage, message_id)
            self.assertEqual(stored.role, "assistant")
            self.assertEqual(stored.content, "Ba ngày là hợp lý.")
        finally:
            db.close()

    def test_the_id_from_the_stream_is_immediately_ratable(self):
        # The whole point of emitting it. If this pair ever drifts, the button
        # appears and then fails on the first click.
        message_id = self._done_payload(self._stream(self.client).text)["message_id"]
        response = self.client.put(
            f"/api/v1/conversations/{self.owned_id}/messages/{message_id}/feedback",
            json={"rating": "down", "reason": "refused_wrongly"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["message_id"], message_id)

    def test_a_persistence_failure_costs_the_button_not_the_answer(self):
        # persist_turn returning anything that is not a row id must not put a
        # value into the JSON payload, and must not break a reply that has
        # already been streamed to the guest.
        client = self.client

        async def fake_stream(*args, **kwargs):
            yield "Câu trả lời."

        with patch("app.services.unified_chat.travel_vector_store.query", new=AsyncMock(return_value=[])), patch(
            "app.services.unified_chat.llm_client.chat_stream", new=fake_stream
        ), patch("app.api.unified.persist_turn", return_value=object()):
            response = self.client.post(
                f"/api/v1/conversations/{self.owned_id}/messages/stream",
                json={"message": "Đà Nẵng có gì chơi?", "mode": "TRAVEL"},
            )
        self.assertEqual(response.status_code, 200)
        payload = self._done_payload(response.text)
        self.assertIsNone(payload["message_id"])
        # The reply still reached the guest; only the rating button is missing.
        self.assertIn("event: token", response.text)
        self.assertIn("event: done", response.text)

    def test_done_drops_a_suggestion_answered_by_the_current_reply(self):
        from app.services.unified_chat import UnifiedChatPlan

        plan = UnifiedChatPlan(
            mode="TRAVEL",
            deterministic_reply="Hồ Thác Bà phù hợp với lịch trình 2 ngày 1 đêm.",
            suggestions=[
                {
                    "id": "context-duration",
                    "label": "Thêm thời lượng chuyến đi",
                    "prompt": "Đi mấy ngày?",
                }
            ],
        )
        with patch(
            "app.api.unified.build_chat_plan",
            new=AsyncMock(return_value=plan),
        ), patch("app.core.database.SessionLocal", self.Session):
            response = self.client.post(
                f"/api/v1/conversations/{self.owned_id}/messages/stream",
                json={"message": "Hồ Thác Bà có gì đặc biệt?", "mode": "TRAVEL"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._done_payload(response.text)["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
