import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth, unified
from app.core.database import Base, get_db
from app.models import auth as auth_models  # noqa: F401
from app.models import destination, hotel, knowledge, unified as unified_models  # noqa: F401
from app.services.demo_seed import seed_unified_demo
from app.services.unified_chat import (
    _rank_knowledge,
    _service_action,
    build_chat_plan,
)


class UnifiedMVPTests(unittest.TestCase):
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
        self.assertEqual(session.status_code, 200)
        self.client.headers.update({"X-CSRF-Token": session.json()["csrf_token"]})

    def tearDown(self):
        self.engine.dispose()

    def _verified_headers(self):
        connected = self.client.post(
            "/api/v1/stays/connect",
            json={"connect_code": "demo-an-nhien-305"},
        )
        self.assertEqual(connected.status_code, 200)
        verified = self.client.post(
            "/api/v1/stays/verify",
            json={"connection_id": connected.json()["connection_id"], "pin": "3056"},
        )
        self.assertEqual(verified.status_code, 200)
        return {"X-Stay-Token": verified.json()["access_token"]}

    def test_connect_rejects_wrong_pin_then_accepts_correct_pin(self):
        connected = self.client.post(
            "/api/v1/stays/connect",
            json={"connect_code": "demo-an-nhien-305"},
        )
        connection_id = connected.json()["connection_id"]
        wrong = self.client.post(
            "/api/v1/stays/verify",
            json={"connection_id": connection_id, "pin": "0000"},
        )
        self.assertEqual(wrong.status_code, 401)
        verified = self.client.post(
            "/api/v1/stays/verify",
            json={"connection_id": connection_id, "pin": "3056"},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["stay"]["room_number"], "305")

    def test_hotel_mode_requires_stay_token(self):
        conversation = self.client.post("/api/v1/conversations", json={}).json()
        response = self.client.post(
            f"/api/v1/conversations/{conversation['id']}/messages/stream",
            json={"message": "Mật khẩu Wi-Fi là gì?", "mode": "HOTEL"},
        )
        self.assertEqual(response.status_code, 403)

    def test_guest_can_revoke_device_access(self):
        headers = self._verified_headers()
        revoked = self.client.delete("/api/v1/stays/active", headers=headers)
        self.assertEqual(revoked.status_code, 204)
        active = self.client.get("/api/v1/stays/active", headers=headers)
        self.assertFalse(active.json()["active"])

    def test_service_request_requires_confirmation_and_is_idempotent(self):
        headers = self._verified_headers()
        services = self.client.get("/api/v1/hotel/services", headers=headers).json()
        service = next(item for item in services if item["code"] == "svc_water")
        payload = {
            "service_item_id": service["id"],
            "quantity": 1,
            "note": "Để trước cửa",
            "confirmed": False,
            "idempotency_key": "fixed-demo-key-001",
        }
        rejected = self.client.post("/api/v1/service-requests", headers=headers, json=payload)
        self.assertEqual(rejected.status_code, 409)
        payload["confirmed"] = True
        first = self.client.post("/api/v1/service-requests", headers=headers, json=payload)
        second = self.client.post("/api/v1/service-requests", headers=headers, json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_consent_is_purpose_specific_and_withdrawable(self):
        conversation = self.client.post("/api/v1/conversations", json={}).json()
        path = f"/api/v1/conversations/{conversation['id']}/consents"
        granted = self.client.put(
            path,
            json={"purpose": "USAGE_ANALYTICS", "status": "GRANTED"},
        )
        self.assertEqual(granted.status_code, 200)
        withdrawn = self.client.put(
            path,
            json={"purpose": "USAGE_ANALYTICS", "status": "WITHDRAWN"},
        )
        self.assertEqual(withdrawn.json()["status"], "WITHDRAWN")
        self.assertIsNotNone(withdrawn.json()["withdrawn_at"])

    def test_demo_hotel_chat_returns_action_without_llm(self):
        headers = self._verified_headers()
        conversation = self.client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"preferred_mode": "HOTEL"},
        ).json()
        with patch("app.api.unified.persist_turn"):
            response = self.client.post(
                f"/api/v1/conversations/{conversation['id']}/messages/stream",
                headers=headers,
                json={"message": "Cho tôi thêm hai chai nước", "mode": "HOTEL"},
            )
        self.assertEqual(response.status_code, 200)
        events = response.text
        self.assertIn("event: action", events)
        self.assertIn("SERVICE_REQUEST_DRAFT", events)
        self.assertIn("svc_water", events)

    def test_chat_reply_follows_english_locale(self):
        conversation = self.client.post(
            "/api/v1/conversations",
            json={"locale": "en"},
        ).json()
        with patch("app.api.unified.persist_turn"):
            response = self.client.post(
                f"/api/v1/conversations/{conversation['id']}/messages/stream",
                json={"message": "hello", "mode": "TRAVEL", "locale": "en"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("I can help you explore destinations", response.text)

    def test_omitted_locale_preserves_conversation_language_and_explicit_override_applies(self):
        english = self.client.post(
            "/api/v1/conversations",
            json={"locale": "en"},
        ).json()
        with patch("app.api.unified.persist_turn"):
            omitted = self.client.post(
                f"/api/v1/conversations/{english['id']}/messages/stream",
                json={"message": "hello", "mode": "TRAVEL"},
            )
        self.assertEqual(omitted.status_code, 200)
        self.assertIn("I can help you explore destinations", omitted.text)
        self.assertEqual(
            self.client.get(f"/api/v1/conversations/{english['id']}").json()["locale"],
            "en",
        )

        chinese = self.client.post(
            "/api/v1/conversations",
            json={"locale": "zh-Hans"},
        ).json()
        with patch("app.api.unified.persist_turn"):
            preserved = self.client.post(
                f"/api/v1/conversations/{chinese['id']}/messages/stream",
                json={"message": "你好", "mode": "TRAVEL"},
            )
        self.assertEqual(preserved.status_code, 200)
        self.assertIn("我可以帮助你探索目的地", preserved.text)
        self.assertEqual(
            self.client.get(f"/api/v1/conversations/{chinese['id']}").json()["locale"],
            "zh-Hans",
        )

        with patch("app.api.unified.persist_turn"):
            explicit = self.client.post(
                f"/api/v1/conversations/{english['id']}/messages/stream",
                json={"message": "Xin chào", "mode": "TRAVEL", "locale": "vi"},
            )
        self.assertEqual(explicit.status_code, 200)
        self.assertIn("Xin chào! Mình có thể hỗ trợ", explicit.text)
        self.assertEqual(
            self.client.get(f"/api/v1/conversations/{english['id']}").json()["locale"],
            "vi",
        )

    def test_hotel_action_follows_simplified_chinese_locale(self):
        headers = self._verified_headers()
        conversation = self.client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"preferred_mode": "HOTEL", "locale": "zh-Hans"},
        ).json()
        with patch("app.api.unified.persist_turn"):
            response = self.client.post(
                f"/api/v1/conversations/{conversation['id']}/messages/stream",
                headers=headers,
                json={
                    "message": "我想要两瓶饮用水",
                    "mode": "HOTEL",
                    "locale": "zh-Hans",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("增加两瓶 500 ml 饮用水", response.text)
        self.assertIn("SERVICE_REQUEST_DRAFT", response.text)

    def test_simplified_chinese_greeting_uses_selected_locale(self):
        conversation = self.client.post(
            "/api/v1/conversations",
            json={"locale": "zh-Hans"},
        ).json()
        with patch("app.api.unified.persist_turn"):
            response = self.client.post(
                f"/api/v1/conversations/{conversation['id']}/messages/stream",
                json={"message": "你好", "mode": "TRAVEL", "locale": "zh-Hans"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("我可以帮助你探索目的地", response.text)

    def test_explicit_null_clears_stale_travel_destination_scope(self):
        conversation = self.client.post(
            "/api/v1/conversations",
            json={"destination_slug": "demo-da-nang"},
        ).json()
        with patch("app.api.unified.persist_turn"):
            response = self.client.post(
                f"/api/v1/conversations/{conversation['id']}/messages/stream",
                json={
                    "message": "Xin chào",
                    "mode": "TRAVEL",
                    "destination_slug": None,
                },
            )
        self.assertEqual(response.status_code, 200)
        refreshed = self.client.get(
            f"/api/v1/conversations/{conversation['id']}"
        ).json()
        self.assertIsNone(refreshed["destination_slug"])

    def _seeded_hotel_and_conversation(self):
        db = self.Session()
        hotel_row = db.query(hotel.Hotel).one()
        conversation = unified_models.UnifiedConversation(
            country_code="VN",
            preferred_mode="TRAVEL",
            locale="vi",
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return db, hotel_row, conversation

    @staticmethod
    def _knowledge_item(item_id, *, title, content, category="travel", sort_order=0):
        return unified_models.HotelKnowledgeItem(
            id=item_id,
            hotel_id="hotel_demo_an_nhien",
            category=category,
            title=title,
            content=content,
            visibility="PUBLIC",
            sort_order=sort_order,
        )

    def test_knowledge_requires_two_shared_tokens_but_keeps_exact_topics(self):
        beach = self._knowledge_item(
            "beach",
            title="Biển Mỹ Khê",
            content="Biển Mỹ Khê cách khách sạn 1.4 km.",
        )
        wifi = self._knowledge_item(
            "wifi",
            title="Thông tin Wi-Fi",
            content="Tên mạng AnNhien-Guest-DEMO. Mật khẩu demo-wifi-2026.",
            category="wifi",
        )

        self.assertEqual(_rank_knowledge("Tìm biển yên tĩnh", [beach]), [])
        self.assertEqual(_rank_knowledge("Biển Mỹ Khê", [beach]), [beach])
        self.assertEqual(_rank_knowledge("Wi-Fi", [wifi]), [wifi])
        self.assertEqual(_rank_knowledge("Mật khẩu Wi-Fi là gì?", [wifi]), [wifi])
        self.assertEqual(_rank_knowledge("Wi-Fi sân bay", [wifi]), [])

    def test_service_matching_uses_complete_tokens_and_phrases(self):
        db, hotel_row, conversation = self._seeded_hotel_and_conversation()
        try:
            services = db.query(unified_models.HotelServiceItem).filter(
                unified_models.HotelServiceItem.hotel_id == hotel_row.id,
                unified_models.HotelServiceItem.is_active.is_(True),
            ).all()
            laundry = next(item for item in services if item.code == "svc_laundry")

            self.assertEqual(
                _service_action("Tôi muốn giặt ủi", services).code,
                "svc_laundry",
            )

            weak = asyncio.run(
                build_chat_plan(
                    db,
                    conversation,
                    "Giặt áo sơ mi",
                    "HOTEL",
                    verified_hotel=hotel_row,
                )
            )
            self.assertIsNone(weak.deterministic_reply)

            valid = asyncio.run(
                build_chat_plan(
                    db,
                    conversation,
                    "Giá giặt áo sơ mi bao nhiêu?",
                    "HOTEL",
                    verified_hotel=hotel_row,
                )
            )
            self.assertIn("50.000", valid.deterministic_reply)
            self.assertEqual(laundry.code, "svc_laundry")
        finally:
            db.close()

    def test_travel_rag_wins_over_demo_knowledge(self):
        db, hotel_row, conversation = self._seeded_hotel_and_conversation()
        try:
            rag_chunk = {
                "text": "Nguồn Travel thật cho Biển Mỹ Khê.",
                "source": "travel-guide.pdf",
                "metadata": {"section": "beaches"},
            }
            with patch(
                "app.services.unified_chat.travel_vector_store.query",
                new=AsyncMock(return_value=[rag_chunk]),
            ) as query:
                plan = asyncio.run(
                    build_chat_plan(
                        db,
                        conversation,
                        "Biển Mỹ Khê",
                        "TRAVEL",
                        verified_hotel=hotel_row,
                    )
                )

            query.assert_awaited_once()
            self.assertIsNone(plan.deterministic_reply)
            self.assertEqual(plan.sources[0]["source"], "travel-guide.pdf")
            self.assertIn(rag_chunk["text"], plan.llm_messages[0]["content"])
            self.assertNotIn("Dữ liệu khách sạn demo", plan.llm_messages[0]["content"])
        finally:
            db.close()

    def test_travel_demo_fallback_handles_empty_and_failed_rag(self):
        for retrieval in ([], RuntimeError("retrieval offline")):
            db, hotel_row, conversation = self._seeded_hotel_and_conversation()
            try:
                mock_query = AsyncMock(
                    side_effect=retrieval
                    if isinstance(retrieval, Exception)
                    else None,
                    return_value=None if isinstance(retrieval, Exception) else retrieval,
                )
                with patch(
                    "app.services.unified_chat.travel_vector_store.query",
                    new=mock_query,
                ):
                    plan = asyncio.run(
                        build_chat_plan(
                            db,
                            conversation,
                            "Biển Mỹ Khê",
                            "TRAVEL",
                            verified_hotel=hotel_row,
                        )
                    )

                self.assertEqual(mock_query.await_count, 1)
                self.assertIsNotNone(plan.deterministic_reply)
                self.assertIn("Biển Mỹ Khê", plan.deterministic_reply)
                self.assertEqual(plan.sources[0]["source"], "Dữ liệu khách sạn demo")
            finally:
                db.close()

    def test_travel_llm_fallback_excludes_hotel_only_context(self):
        db, hotel_row, conversation = self._seeded_hotel_and_conversation()
        try:
            with patch(
                "app.services.unified_chat.travel_vector_store.query",
                new=AsyncMock(return_value=[]),
            ):
                plan = asyncio.run(
                    build_chat_plan(
                        db,
                        conversation,
                        "Câu hỏi không khớp dữ liệu",
                        "TRAVEL",
                        verified_hotel=hotel_row,
                    )
                )
            context = plan.llm_messages[0]["content"]
            self.assertIn("Biển Mỹ Khê", context)
            self.assertNotIn("Thông tin Wi-Fi", context)
            self.assertNotIn("demo-wifi-2026", context)
        finally:
            db.close()

    def test_travel_policy_uses_verbatim_single_source_reply(self):
        db, hotel_row, conversation = self._seeded_hotel_and_conversation()
        try:
            policy_chunk = {
                "text": (
                    "Tên chương trình: Hồ Thác Bà 1 ngày\n"
                    "Điểm đến liên quan: Hồ Thác Bà\n"
                    "Phần: Policy\n\n"
                    "QUY ĐỊNH HỦY TOUR\n"
                    "Nếu quý khách hủy tour từ 05-07 ngày trước ngày khởi hành: phí hủy 20% phí cọc."
                ),
                "source": "HVNT 1N HỒ THÁC BÀ.pdf",
                "metadata": {
                    "filename": "HVNT 1N HỒ THÁC BÀ.pdf",
                    "section": "Policy",
                    "document_id": "doc-1",
                    "document_version_id": "version-1",
                    "chunk_index": 1,
                },
            }
            with patch(
                "app.services.unified_chat.travel_vector_store.query",
                new=AsyncMock(return_value=[policy_chunk]),
            ), patch("app.services.unified_chat.llm_client.chat_stream") as llm_stream:
                plan = asyncio.run(
                    build_chat_plan(
                        db,
                        conversation,
                        "HVNT 1N HỒ THÁC BÀ hủy tour thì mất bao nhiêu?",
                        "TRAVEL",
                        verified_hotel=hotel_row,
                    )
                )

            self.assertEqual(plan.llm_messages, [])
            self.assertIn("HVNT 1N HỒ THÁC BÀ.pdf", plan.deterministic_reply)
            self.assertIn(
                "Nếu quý khách hủy tour từ 05-07 ngày trước ngày khởi hành: phí hủy 20% phí cọc.",
                plan.deterministic_reply,
            )
            self.assertNotIn("hoàn 100%", plan.deterministic_reply)
            self.assertEqual(len(plan.sources), 1)
            llm_stream.assert_not_called()
        finally:
            db.close()

    def test_short_followup_uses_recent_explicit_language_in_prompt(self):
        db, hotel_row, conversation = self._seeded_hotel_and_conversation()
        try:
            db.add_all(
                [
                    unified_models.UnifiedMessage(
                        conversation_id=conversation.id,
                        role="user",
                        context_type="TRAVEL",
                        content="Please answer in English.",
                    ),
                    unified_models.UnifiedMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        context_type="TRAVEL",
                        content="Sure.",
                    ),
                ]
            )
            db.commit()
            with patch(
                "app.services.unified_chat.travel_vector_store.query",
                new=AsyncMock(return_value=[]),
            ):
                plan = asyncio.run(
                    build_chat_plan(
                        db,
                        conversation,
                        "And Wi-Fi?",
                        "TRAVEL",
                        verified_hotel=hotel_row,
                    )
                )
            prompt = plan.llm_messages[0]["content"]
            self.assertIn("in English", prompt)
            self.assertNotIn("in Vietnamese", prompt)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
