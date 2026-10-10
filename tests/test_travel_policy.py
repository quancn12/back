import unittest
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.travel_chat import _prepare_chat, travel_chat, travel_chat_stream
from app.core.config import settings
from app.core.database import Base
from app.models.destination import Destination
from app.models.knowledge import DocumentChunk, DocumentVersion, SourceDocument
from app.models.schemas import TravelChatRequest
from app.services import travel_ingestion as ingestion
from app.services.source_storage import LocalSourceStorage
from app.services.travel_policy import source_faithful_policy_reply
from app.services.travel_vector_store import TravelVectorStore, is_policy_query


class TravelPolicyTests(unittest.TestCase):
    POLICY_CHUNK = {
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

    def test_policy_query_only_ranks_policy_chunks(self):
        candidates = [
            {
                "id": "itinerary",
                "text": "Tour itinerary mentions cancellation as an incidental word.",
                "metadata": {"filename": "HVNT 0001-24.pdf", "section": "Lịch Trình"},
                "distance": 0.1,
                "keyword_score": 8,
                "vector_rank": 0,
            },
            {
                "id": "policy",
                "text": "QUY ĐỊNH HỦY TOUR\nHủy tour trước 05 ngày: phí 20%.",
                "metadata": {"filename": "HVNT 0001-24.pdf", "section": "Policy"},
                "distance": 0.8,
                "keyword_score": 2,
                "vector_rank": 1,
            },
            {
                "id": "other-policy",
                "text": "QUY ĐỊNH HỦY TOUR: phí hoàn 100%.",
                "metadata": {"filename": "HVNT 0002-24.pdf", "section": "Policy"},
                "distance": 0.9,
                "keyword_score": 4,
                "vector_rank": 2,
            },
        ]
        self.assertTrue(is_policy_query("HVNT 0001-24 hủy tour trước ngày đi thế nào?"))
        self.assertTrue(is_policy_query("Can I cancel the tour and get my deposit back?"))
        self.assertTrue(is_policy_query("如果取消行程，可以退多少钱？"))
        self.assertTrue(is_policy_query("旅行团改期要收费吗？"))
        self.assertFalse(is_policy_query("điều kiện thời tiết ở Hồ Thác Bà"))
        self.assertFalse(is_policy_query("chính sách visa du lịch Việt Nam"))
        self.assertFalse(is_policy_query("What are the weather conditions in Sapa?"))
        self.assertFalse(is_policy_query("越南的签证政策是什么？"))
        self.assertEqual(
            [item["id"] for item in TravelVectorStore._rank(
                candidates, top_k=5, query_text="HVNT 0001-24 hủy tour trước ngày đi thế nào?"
            )],
            ["policy"],
        )
        self.assertEqual(
            [item["id"] for item in TravelVectorStore._rank(
                candidates, top_k=5, query_text="tour đi những đâu?"
            )],
            ["itinerary"],
        )

    def test_current_turn_policy_intent_overrides_policy_words_in_memory_hint(self):
        candidates = [
            {
                "id": "itinerary",
                "text": "08h00: Khởi hành đi Hồ Thác Bà.",
                "metadata": {"section": "Lịch Trình"},
                "distance": 0.2,
                "keyword_score": 2,
                "vector_rank": 0,
            },
            {
                "id": "policy",
                "text": "Hủy tour trước 05 ngày: phí 20%.",
                "metadata": {"section": "Policy"},
                "distance": 0.1,
                "keyword_score": 3,
                "vector_rank": 1,
            },
        ]

        ranked = TravelVectorStore._rank(
            candidates,
            top_k=5,
            query_text="Tour đi những đâu? Relevant prior constraint: hủy tour",
            policy_query=False,
        )

        self.assertEqual(["itinerary"], [item["id"] for item in ranked])

    def test_legacy_followup_passes_current_turn_policy_intent_to_retrieval(self):
        session = SimpleNamespace(
            session_id="session-1",
            history=[
                {"role": "user", "content": "HVTB 0004-23 hủy tour thế nào?"},
                {"role": "assistant", "content": "Bạn xác nhận đúng tour trước nhé."},
            ],
        )
        payload = TravelChatRequest(
            message="Tour đi những đâu?", session_id=session.session_id
        )
        query = AsyncMock(return_value=[])

        with (
            patch(
                "app.api.travel_chat.travel_session_store.get_or_create",
                return_value=session,
            ),
            patch("app.api.travel_chat.travel_vector_store.query", new=query),
        ):
            asyncio.run(_prepare_chat(payload, MagicMock()))

        self.assertIn("hủy tour", query.await_args.kwargs["query_text"])
        self.assertFalse(query.await_args.kwargs["policy_query"])

    def test_answer_quotes_one_source_without_combining_sibling_tour_terms(self):
        chunks = [
            {
                "text": (
                    "Tên chương trình: Hồ Thác Bà 1 ngày\n"
                    "Điểm đến liên quan: Hồ Thác Bà\n"
                    "Phần: Policy\n\n"
                    "CÁC QUY ĐỊNH ÁP DỤNG CHO CHƯƠNG TRÌNH\n"
                    "QUY ĐỊNH HỦY TOUR\n"
                    "Nếu quý khách hủy tour từ 05-07 ngày trước ngày khởi hành: phí hủy 20% phí cọc."
                ),
                "metadata": {
                    "filename": "HVNT 1N HỒ THÁC BÀ.pdf",
                    "section": "Policy",
                    "document_id": "doc-1",
                    "document_version_id": "version-1",
                    "chunk_index": 1,
                },
            },
            {
                "text": "Điều khoản tour khác: hoàn 100%.",
                "metadata": {"filename": "HVNT 0006-23.pdf", "section": "Policy"},
            },
        ]
        reply = source_faithful_policy_reply(
            chunks, question="HVNT 1N HỒ THÁC BÀ hủy tour"
        )
        self.assertIn("chưa thể xác minh", reply)
        self.assertNotIn("HVNT 1N HỒ THÁC BÀ.pdf", reply)
        self.assertNotIn("hoàn 100%", reply)

    def test_same_source_chunks_are_joined_in_order_and_versions_are_isolated(self):
        first = dict(self.POLICY_CHUNK)
        first["metadata"] = {
            **first["metadata"], "document_id": "doc-1",
            "document_version_id": "version-1", "chunk_index": 10,
        }
        second = {
            "text": (
                "Tên chương trình: Hồ Thác Bà 1 ngày\n"
                "Điểm đến liên quan: Hồ Thác Bà\n"
                "Phần: Policy\n\n"
                "tour từ 05-07 ngày trước ngày khởi hành: phí hủy 20% phí cọc.\n"
                "THÔNG TIN QUAN TRỌNG CẦN LƯU Ý"
            ),
            "metadata": {
                "filename": "HVNT 1N HỒ THÁC BÀ.pdf", "section": "Policy",
                "document_id": "doc-1", "document_version_id": "version-1",
                "chunk_index": 11,
            },
        }
        old_version = {
            "text": "ĐIỀU KHOẢN TOUR CŨ: hoàn 100%.",
            "metadata": {
                "filename": "HVNT 1N HỒ THÁC BÀ.pdf", "section": "Policy",
                "document_id": "doc-1", "document_version_id": "version-0",
                "chunk_index": 12,
            },
        }
        reply = source_faithful_policy_reply(
            [second, first], question="HVNT 1N HỒ THÁC BÀ hủy tour"
        )
        self.assertIn("QUY ĐỊNH HỦY TOUR", reply)
        self.assertIn("THÔNG TIN QUAN TRỌNG CẦN LƯU Ý", reply)
        self.assertNotIn("tour cũ", reply.lower())
        self.assertNotIn("hoàn 100%", reply)
        self.assertIn("phí hủy 20% phí cọc", reply.lower())
        self.assertIn("hủy tour", reply.lower())
        self.assertIn(
            "chưa thể xác minh", source_faithful_policy_reply(
                [first, old_version], question="HVNT 1N HỒ THÁC BÀ hủy tour"
            )
        )

    def test_missing_policy_provenance_fails_closed(self):
        reply = source_faithful_policy_reply([
            {"text": "HỦY TOUR: hoàn 100%.", "metadata": {"section": "Policy"}}
        ])
        self.assertIn("đủ thông tin tour và phiên bản", reply)
        self.assertNotIn("hoàn 100%", reply)

    def test_filename_is_rendered_as_code_not_markdown(self):
        chunk = dict(self.POLICY_CHUNK)
        chunk["metadata"] = {
            **chunk["metadata"], "filename": "[tour](https://example.invalid).pdf"
        }
        reply = source_faithful_policy_reply(
            [chunk], question="tour https example invalid pdf cancel"
        )
        self.assertIn("`[tour](https://example.invalid).pdf`", reply)

    def test_localized_intro_keeps_source_terms_verbatim(self):
        english = source_faithful_policy_reply(
            [self.POLICY_CHUNK], "en", question="HVNT 1N HỒ THÁC BÀ cancel tour"
        )
        chinese = source_faithful_policy_reply(
            [self.POLICY_CHUNK], "zh-Hans", question="HVNT 1N HỒ THÁC BÀ 取消行程"
        )
        exact_source_line = "Nếu quý khách hủy tour từ 05-07 ngày trước ngày khởi hành: phí hủy 20% phí cọc."
        self.assertIn("According to", english)
        self.assertIn("根据", chinese)
        self.assertIn(exact_source_line, english)
        self.assertIn(exact_source_line, chinese)

    def test_oversized_policy_uses_vietnamese_fallback(self):
        chunk = dict(self.POLICY_CHUNK)
        chunk["text"] = (
            "Tên chương trình: Hồ Thác Bà 1 ngày\n"
            "Phần: Policy\n\n" + "A" * 4001
        )
        reply = source_faithful_policy_reply(
            [chunk], question="HVNT 1N HỒ THÁC BÀ hủy tour"
        )
        self.assertIn("quá dài", reply)
        self.assertIn("Bạn hãy hỏi từng điều khoản", reply)

    @unittest.skipUnless((Path(__file__).resolve().parents[1] / "data" / "HỒ THÁC BÀ").is_dir(),
                         "real tour PDFs are not published in this repository")
    def test_sqlite_ingestion_and_retrieval_quote_the_named_real_tour(self):
        project_root = Path(__file__).resolve().parents[1]
        pdf = (
            project_root
            / "data"
            / "HỒ THÁC BÀ"
            / "HVTB 0006-23 2N1Đ YÊN BÁI - HỒ THÁC BÀ - THUỶ ĐIỆN - ĐẢO XANH - ĐẢO THIÊN ĐƯỜNG - KHU DU LỊCH SINH THÁI RUBY.pdf"
        )
        self.assertTrue(pdf.is_file())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = create_engine(
                f"sqlite:///{(root / 'travel-policy.db').as_posix()}",
                connect_args={"check_same_thread": False},
            )
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine)
            storage = LocalSourceStorage(root / "sources")
            db = Session()
            try:
                destination = Destination(
                    slug="policy-integration-thac-ba",
                    name="Hồ Thác Bà",
                    destination_type="region",
                    country_code="VN",
                    country_name="Việt Nam",
                    province_name="Lào Cai",
                )
                db.add(destination)
                db.commit()

                session_patch = patch.object(ingestion, "SessionLocal", Session)
                storage_patch = patch.object(ingestion, "source_storage", storage)
                vector_session_patch = patch(
                    "app.services.travel_vector_store.SessionLocal", Session
                )
                vector_engine_patch = patch(
                    "app.services.travel_vector_store.engine", engine
                )

                async def fake_embeddings(texts):
                    return [
                        [1.0] + [0.0] * (settings.embedding_dimensions - 1)
                        for _ in texts
                    ]

                async def fake_query_embedding(_text):
                    return [1.0] + [0.0] * (settings.embedding_dimensions - 1)

                question = "HVTB 0006-23 hủy tour trước ngày khởi hành mất bao nhiêu?"
                with (
                    session_patch,
                    storage_patch,
                    vector_session_patch,
                    vector_engine_patch,
                    patch.object(ingestion, "_embed_chunks", fake_embeddings),
                    patch("app.services.travel_vector_store.llm_client.embed", new=AsyncMock(side_effect=fake_query_embedding)),
                    patch.object(settings, "database_url", f"sqlite:///{(root / 'travel-policy.db').as_posix()}"),
                ):
                    job, version, changed = ingestion.enqueue_pdf_path(
                        path=pdf,
                        destination=destination,
                        db=db,
                        source_key=f"folder:policy-integration:{pdf.name}",
                        filename=pdf.name,
                    )
                    self.assertTrue(changed)
                    self.assertIsNotNone(job)
                    asyncio.run(ingestion.process_job(job.id))
                    db.expire_all()
                    source = db.query(SourceDocument).one()
                    active_version = db.query(DocumentVersion).filter_by(
                        id=version.id
                    ).one()
                    policy_count = db.query(DocumentChunk).filter_by(
                        document_version_id=version.id, section="Policy"
                    ).count()
                    self.assertGreater(policy_count, 0)
                    self.assertTrue(active_version.is_active)

                    sibling = SourceDocument(
                        id="sibling-source",
                        source_key="folder:sibling-tour.pdf",
                        primary_destination_id=destination.id,
                        filename="HVNT SIBLING TOUR.pdf",
                        storage_path="unused.pdf",
                        status="active",
                    )
                    sibling_version = DocumentVersion(
                        id="sibling-version",
                        source_document_id=sibling.id,
                        version=1,
                        content_hash="sibling-hash",
                        storage_path="unused.pdf",
                        status="done",
                        is_active=True,
                        chunk_count=1,
                        embedding_model=settings.ollama_embed_model,
                        chunker_version=ingestion.CHUNKER_VERSION,
                    )
                    db.add_all([sibling, sibling_version])
                    db.flush()
                    db.add(
                        DocumentChunk(
                            id="sibling-policy-chunk",
                            source_document_id=sibling.id,
                            document_version_id=sibling_version.id,
                            chunk_index=0,
                            section="Policy",
                            text="SIBLING ONLY: hoàn 99% giá tour.",
                            search_text="sibling only hoan 99 gia tour",
                            embedding=[1.0] + [0.0] * (settings.embedding_dimensions - 1),
                            country_code="VN",
                            destination_slug=destination.slug,
                            destination_name=destination.name,
                            province_name=destination.province_name,
                            filename=sibling.filename,
                            locations="Hồ Thác Bà",
                            chunk_metadata={
                                "document_id": sibling.id,
                                "document_version_id": sibling_version.id,
                                "filename": sibling.filename,
                                "source": sibling.filename,
                                "section": "Policy",
                            },
                        )
                    )
                    db.commit()

                    chunks = asyncio.run(
                        TravelVectorStore().query(
                            query_text=question,
                            country_code="VN",
                            destination_slug=destination.slug,
                            top_k=6,
                        )
                    )
                    self.assertTrue(chunks)
                    reply = source_faithful_policy_reply(chunks, question=question)

                self.assertEqual(source.filename, pdf.name)
                self.assertIn(pdf.name, reply)
                self.assertIn("100% giá trị tour", reply)
                self.assertNotIn("SIBLING ONLY", reply)
                self.assertNotIn("99%", reply)
            finally:
                db.close()
                engine.dispose()

    def test_missing_policy_evidence_asks_for_tour_name(self):
        reply = source_faithful_policy_reply([
            {"text": "Nơi đến: Hồ Thác Bà", "metadata": {"section": "Tổng Quan"}}
        ])
        self.assertIn("mã hoặc tên tour", reply)

    def test_generic_cancellation_question_does_not_assume_retrieved_tour(self):
        reply = source_faithful_policy_reply(
            [self.POLICY_CHUNK], question="Hủy tour mất phí bao nhiêu?"
        )
        self.assertIn("xác nhận đây là tour", reply)
        self.assertIn("HVNT 1N HỒ THÁC BÀ.pdf", reply)
        self.assertNotIn("20%", reply)

    def test_travel_json_endpoint_returns_policy_without_calling_llm(self):
        session = SimpleNamespace(session_id="session-1", add_turn=MagicMock())
        db = MagicMock()
        payload = TravelChatRequest(message="HVNT 1N HỒ THÁC BÀ hủy tour mất phí bao nhiêu?")
        prepared = (session, [self.POLICY_CHUNK], [], "VN", None, payload.message)
        with patch("app.api.travel_chat._prepare_chat", new=AsyncMock(return_value=prepared)), \
             patch("app.api.travel_chat.llm_client.chat", new=AsyncMock()) as llm_chat:
            response = asyncio.run(travel_chat(payload, db))
        self.assertIn("HVNT 1N HỒ THÁC BÀ.pdf", response.reply)
        self.assertIn("phí hủy 20% phí cọc", response.reply)
        llm_chat.assert_not_awaited()
        self.assertEqual(session.add_turn.call_count, 2)

    def test_travel_json_policy_reply_uses_inferred_english_locale(self):
        session = SimpleNamespace(session_id="session-1", add_turn=MagicMock())
        payload = TravelChatRequest(
            message="Can I cancel the tour HVNT 1N HỒ THÁC BÀ?"
        )
        prepared = (session, [self.POLICY_CHUNK], [], "VN", None, payload.message)
        with patch(
            "app.api.travel_chat._prepare_chat",
            new=AsyncMock(return_value=prepared),
        ):
            response = asyncio.run(travel_chat(payload, MagicMock()))
        self.assertIn("According to", response.reply)
        self.assertIn("phí hủy 20% phí cọc", response.reply)

    def test_travel_json_endpoint_asks_which_tour_before_quoting_generic_query(self):
        session = SimpleNamespace(session_id="session-1", add_turn=MagicMock())
        db = MagicMock()
        payload = TravelChatRequest(message="Hủy tour mất phí bao nhiêu?")
        prepared = (session, [self.POLICY_CHUNK], [], "VN", None, payload.message)
        with patch("app.api.travel_chat._prepare_chat", new=AsyncMock(return_value=prepared)):
            response = asyncio.run(travel_chat(payload, db))
        self.assertIn("xác nhận đây là tour", response.reply)
        self.assertNotIn("20%", response.reply)

    def test_travel_sse_endpoint_replaces_with_policy_quote_without_tokens(self):
        session = SimpleNamespace(session_id="session-1", add_turn=MagicMock())
        db = MagicMock()
        payload = TravelChatRequest(message="HVNT 1N HỒ THÁC BÀ hủy tour mất phí bao nhiêu?")
        prepared = (session, [self.POLICY_CHUNK], [], "VN", None, payload.message)

        async def collect(response):
            return [event async for event in response.body_iterator]

        with patch("app.api.travel_chat._prepare_chat", new=AsyncMock(return_value=prepared)), \
             patch("app.api.travel_chat.llm_client.chat_stream") as llm_stream:
            response = asyncio.run(travel_chat_stream(payload, db))
            events = asyncio.run(collect(response))
        self.assertTrue(any('event: replace' in event and 'phí hủy 20% phí cọc' in event for event in events))
        self.assertTrue(any('event: done' in event for event in events))
        self.assertFalse(any('event: token' in event for event in events))
        llm_stream.assert_not_called()
        db.close.assert_called_once()

    def test_travel_sse_policy_reply_uses_inferred_chinese_locale(self):
        session = SimpleNamespace(session_id="session-1", add_turn=MagicMock())
        db = MagicMock()
        payload = TravelChatRequest(message="HVNT 1N HỒ THÁC BÀ 取消行程")
        prepared = (session, [self.POLICY_CHUNK], [], "VN", None, payload.message)

        async def collect(response):
            return [event async for event in response.body_iterator]

        with patch(
            "app.api.travel_chat._prepare_chat",
            new=AsyncMock(return_value=prepared),
        ):
            response = asyncio.run(travel_chat_stream(payload, db))
            events = asyncio.run(collect(response))

        self.assertTrue(any("根据" in event for event in events))
        self.assertTrue(any("phí hủy 20% phí cọc" in event for event in events))


if __name__ == "__main__":
    unittest.main()
