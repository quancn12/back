import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.api.travel_chat import _is_simple_social_message
from app.api.travel_chat import _build_system_prompt, _dedupe_context_chunks
from app.services.travel_vector_store import TravelVectorStore


class QueryOptimizationTests(unittest.IsolatedAsyncioTestCase):
    def test_only_simple_social_messages_skip_retrieval(self):
        self.assertTrue(_is_simple_social_message("Hello!"))
        self.assertTrue(_is_simple_social_message("Cảm ơn bạn"))
        self.assertFalse(_is_simple_social_message("Chào bạn, gợi ý Hồ Thác Bà"))

    async def test_exact_query_embedding_is_reused(self):
        store = TravelVectorStore()
        embed = AsyncMock(return_value=[0.25, 0.5])
        with (
            patch("app.services.travel_vector_store.llm_client.embed", embed),
            patch("app.services.travel_vector_store.settings.embedding_query_cache_size", 2),
        ):
            first = await store._embed_query("Hồ Thác Bà")
            second = await store._embed_query("Hồ Thác Bà")

        self.assertEqual(first, second)
        embed.assert_awaited_once()

    async def test_different_accent_forms_do_not_share_cached_embedding(self):
        store = TravelVectorStore()
        embed = AsyncMock(side_effect=[[0.25, 0.5], [0.75, 1.0]])
        with (
            patch("app.services.travel_vector_store.llm_client.embed", embed),
            patch("app.services.travel_vector_store.settings.embedding_query_cache_size", 2),
        ):
            accented = await store._embed_query("Hà Nội")
            unaccented = await store._embed_query("Ha Noi")

        self.assertEqual(accented, [0.25, 0.5])
        self.assertEqual(unaccented, [0.75, 1.0])
        self.assertEqual(embed.await_args_list[0].args, ("Hà Nội",))
        self.assertEqual(embed.await_args_list[1].args, ("Ha Noi",))

    async def test_query_embedding_cache_is_scoped_to_model(self):
        store = TravelVectorStore()
        embed = AsyncMock(side_effect=[[0.25, 0.5], [0.75, 1.0]])
        with (
            patch("app.services.travel_vector_store.llm_client.embed", embed),
            patch("app.services.travel_vector_store.settings.embedding_query_cache_size", 2),
            patch("app.services.travel_vector_store.settings.ollama_embed_model", "model-a"),
        ):
            first_model = await store._embed_query("Hồ Thác Bà")
        with (
            patch("app.services.travel_vector_store.llm_client.embed", embed),
            patch("app.services.travel_vector_store.settings.embedding_query_cache_size", 2),
            patch("app.services.travel_vector_store.settings.ollama_embed_model", "model-b"),
        ):
            second_model = await store._embed_query("Hồ Thác Bà")

        self.assertEqual(first_model, [0.25, 0.5])
        self.assertEqual(second_model, [0.75, 1.0])
        self.assertEqual(embed.await_count, 2)

    async def test_concurrent_exact_query_misses_share_one_embedding(self):
        store = TravelVectorStore()
        started = asyncio.Event()
        release = asyncio.Event()

        async def embed(_query):
            started.set()
            await release.wait()
            return [0.25, 0.5]

        embed_mock = AsyncMock(side_effect=embed)
        with (
            patch("app.services.travel_vector_store.llm_client.embed", new=embed_mock),
            patch("app.services.travel_vector_store.settings.embedding_query_cache_size", 0),
        ):
            first = asyncio.create_task(store._embed_query("Hồ Thác Bà"))
            await started.wait()
            second = asyncio.create_task(store._embed_query("Hồ Thác Bà"))
            await asyncio.sleep(0)
            release.set()
            self.assertEqual(await asyncio.gather(first, second), [[0.25, 0.5], [0.25, 0.5]])

        self.assertEqual(embed_mock.call_count, 1)

    async def test_cancelled_waiter_does_not_cancel_shared_embedding(self):
        store = TravelVectorStore()
        started = asyncio.Event()
        release = asyncio.Event()

        async def embed(_query):
            started.set()
            await release.wait()
            return [0.25, 0.5]

        with patch("app.services.travel_vector_store.llm_client.embed", new=embed):
            first = asyncio.create_task(store._embed_query("Hồ Thác Bà"))
            await started.wait()
            second = asyncio.create_task(store._embed_query("hồ thác bà"))
            await asyncio.sleep(0)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            release.set()
            self.assertEqual(await second, [0.25, 0.5])

        self.assertEqual(store._inflight_embeddings, {})

    async def test_failed_embedding_is_evicted_for_a_later_retry(self):
        store = TravelVectorStore()
        embed = AsyncMock(side_effect=[RuntimeError("embedding offline"), [0.25, 0.5]])

        with patch("app.services.travel_vector_store.llm_client.embed", new=embed):
            with self.assertRaisesRegex(RuntimeError, "embedding offline"):
                await store._embed_query("Hồ Thác Bà")
            self.assertEqual(await store._embed_query("Hồ Thác Bà"), [0.25, 0.5])

        self.assertEqual(embed.await_count, 2)

    def test_context_dedup_keeps_only_exact_duplicates_out_of_prompt(self):
        chunks = [
            {"text": "Same source paragraph", "source": "guide-a.pdf"},
            {"text": "Same source paragraph", "source": "guide-b.pdf"},
            {"text": "Same source paragraph ", "source": "guide-c.pdf"},
        ]

        deduped = _dedupe_context_chunks(chunks)
        self.assertEqual(
            [(index, chunk["source"]) for index, chunk in deduped],
            [(1, "guide-a.pdf"), (3, "guide-c.pdf")],
        )
        prompt = _build_system_prompt(chunks, None, "Vietnam")
        self.assertEqual(prompt.count("Same source paragraph"), 2)
        self.assertIn("--- Nguồn 3: tài liệu du lịch ---", prompt)
        self.assertEqual(len(chunks), 3)

    def test_rank_deduplicates_exact_text_without_normalizing_distinct_excerpts(self):
        candidates = [
            {"text": "One paragraph", "keyword_score": 2.0, "distance": 0.2, "vector_rank": 0},
            {"text": "One paragraph", "keyword_score": 1.0, "distance": 0.3, "vector_rank": 1},
            {"text": "One  paragraph", "keyword_score": 1.0, "distance": 0.4, "vector_rank": 2},
        ]

        ranked = TravelVectorStore._rank(candidates, top_k=3)
        self.assertEqual([item["text"] for item in ranked], ["One paragraph", "One  paragraph"])

    async def test_keyword_retrieval_survives_embedding_failure(self):
        store = TravelVectorStore()
        keyword_candidate = {
            "id": "tram-tau-1",
            "text": "Trạm Tấu có nội dung tắm khoáng nóng.",
            "metadata": {"source": "yen-bai.pdf"},
            "distance": 1.0,
            "keyword_score": 3.0,
            "vector_rank": 0,
            "source": "yen-bai.pdf",
        }
        with (
            patch.object(
                store,
                "_embed_query",
                AsyncMock(side_effect=RuntimeError("embedding offline")),
            ),
            patch.object(
                store,
                "_query_keyword_only",
                return_value=[keyword_candidate],
            ) as keyword_query,
        ):
            results = await store.query(
                query_text="Tắm khoáng nóng Trạm Tấu",
                country_code="VN",
                top_k=4,
            )

        self.assertEqual(results, [keyword_candidate])
        keyword_query.assert_called_once_with(
            "Tắm khoáng nóng Trạm Tấu", "VN", None
        )


if __name__ == "__main__":
    unittest.main()
