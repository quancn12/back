"""POST /api/chat keeps a conversation by the id it hands back.

A partner calls /api/chat directly and looks for `conversation_id`. The route
answered with `session_id` only; both names now carry the same id, in the
JSON reply and in the stream's `done` event, and either may be sent back.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import travel_chat
from app.core.database import Base, get_db
from app.models import destination, knowledge  # noqa: F401

QUESTION = "Hồ Thác Bà có gì chơi?"
FOLLOW_UP = "Đi thuyền ở đó mất bao lâu?"


class ConversationIdTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(travel_chat.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.prompts: list[list[dict]] = []

        async def model(messages, **_kwargs):
            self.prompts.append(messages)
            return f"Trả lời lượt {len(self.prompts)}."

        async def model_stream(messages, **_kwargs):
            self.prompts.append(messages)
            for part in ("Khoảng ", "2 giờ."):
                yield part

        self.patches = [
            patch("app.services.travel_session_store.SessionLocal", self.Session),
            patch("app.api.travel_chat.travel_vector_store.query", new=AsyncMock(return_value=[])),
            patch("app.api.travel_chat.llm_client.chat", new=model),
            patch("app.api.travel_chat.llm_client.chat_stream", new=model_stream),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def _ask(self, **body):
        response = self.client.post("/api/chat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _history_of_last_prompt(self):
        return [m["content"] for m in self.prompts[-1] if m["role"] in {"user", "assistant"}]

    def test_the_reply_carries_conversation_id_equal_to_session_id(self):
        first = self._ask(message=QUESTION)
        self.assertEqual(first["conversation_id"], first["session_id"])
        self.assertRegex(first["conversation_id"], r"^[0-9a-f-]{36}$")

    def test_sending_conversation_id_back_continues_the_conversation(self):
        first = self._ask(message=QUESTION)
        second = self._ask(message=FOLLOW_UP, conversation_id=first["conversation_id"])
        self.assertEqual(second["conversation_id"], first["conversation_id"])
        self.assertEqual(self._history_of_last_prompt(), [QUESTION, "Trả lời lượt 1.", FOLLOW_UP],
                         "the model sees the earlier turn")

    def test_the_older_session_id_name_still_works(self):
        first = self._ask(message=QUESTION)
        second = self._ask(message=FOLLOW_UP, session_id=first["session_id"])
        self.assertEqual(second["conversation_id"], first["conversation_id"])
        self.assertIn(QUESTION, self._history_of_last_prompt())

    def test_without_an_id_each_question_starts_afresh(self):
        first = self._ask(message=QUESTION)
        second = self._ask(message=FOLLOW_UP)
        self.assertNotEqual(first["conversation_id"], second["conversation_id"])
        self.assertEqual(self._history_of_last_prompt(), [FOLLOW_UP])

    def test_an_unknown_id_starts_a_new_conversation_with_a_new_id(self):
        reply = self._ask(message=QUESTION, conversation_id="khong-ton-tai")
        self.assertNotEqual(reply["conversation_id"], "khong-ton-tai")

    def test_two_different_ids_are_rejected(self):
        response = self.client.post("/api/chat", json={"message": QUESTION, "conversation_id": "a", "session_id": "b"})
        self.assertEqual(response.status_code, 422)

    def test_the_stream_done_event_carries_conversation_id_and_accepts_it(self):
        first = self._ask(message=QUESTION)
        text = self.client.post("/api/chat/stream", json={"message": FOLLOW_UP, "conversation_id": first["conversation_id"]}).text
        done = [json.loads(block.split("data: ", 1)[1]) for block in text.strip().split("\n\n") if block.startswith("event: done")]
        self.assertEqual(done, [{"session_id": first["session_id"], "conversation_id": first["conversation_id"]}])
        self.assertIn(QUESTION, self._history_of_last_prompt())


if __name__ == "__main__":
    unittest.main()
