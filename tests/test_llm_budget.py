import unittest
from unittest.mock import patch

from app.core.config import settings
from app.services.llm_client import (
    context_budget_for,
    LLMClient,
    OllamaProvider,
    output_budget_for,
    requests_extended_output,
)


class _OptionsStreamingOllama:
    def __init__(self):
        self.options = None

    async def chat_stream(self, _messages):
        self.options = OllamaProvider._chat_options()
        yield "Câu trả lời đầy đủ"


class OutputBudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_extended_requests_get_more_room_only_when_conservatively_safe(self):
        messages = [{"role": "user", "content": "Lập lịch trình Yên Bái 5 ngày"}]
        with (
            patch.object(settings, "ollama_num_ctx", 4096),
            patch.object(settings, "ollama_num_predict", 1024),
            patch.object(settings, "ollama_long_num_predict", 1536),
            patch.object(settings, "ollama_context_reserve_tokens", 128),
        ):
            self.assertEqual(output_budget_for(messages, long_request=True), 1536)
            self.assertIsNone(output_budget_for(messages, long_request=False))
            too_large = [{"role": "user", "content": "x" * 12000}]
            self.assertIsNone(output_budget_for(too_large, long_request=True))

    def test_unbounded_default_is_not_replaced_with_a_positive_cap(self):
        with patch.object(settings, "ollama_num_predict", -1):
            self.assertIsNone(output_budget_for([], long_request=True))

    def test_vietnamese_context_estimate_allows_extended_budget(self):
        messages = [{"role": "system", "content": "Điểm đến có khí hậu mát, nhiều cung đường đi bộ và chợ địa phương."}]
        with (
            patch.object(settings, "ollama_num_ctx", 4096),
            patch.object(settings, "ollama_num_predict", 1024),
            patch.object(settings, "ollama_long_num_predict", 1536),
            patch.object(settings, "ollama_context_reserve_tokens", 128),
        ):
            self.assertEqual(output_budget_for(messages, long_request=True), 1536)

    def test_short_turn_uses_reduced_context_when_response_fits(self):
        messages = [{"role": "user", "content": "Giờ mở cửa là mấy giờ?"}]
        with (
            patch.object(settings, "ollama_num_ctx", 4096),
            patch.object(settings, "ollama_short_num_ctx", 2048),
            patch.object(settings, "ollama_num_predict", 1024),
            patch.object(settings, "ollama_context_reserve_tokens", 128),
        ):
            self.assertEqual(context_budget_for(messages, long_request=False), 2048)
            self.assertIsNone(context_budget_for(messages, long_request=True))

    def test_only_explicit_long_form_requests_raise_the_cap(self):
        self.assertTrue(requests_extended_output("Lập lịch trình Yên Bái 5 ngày 4 đêm"))
        self.assertTrue(requests_extended_output("Compare Tram Tau and Suoi Giang in detail"))
        self.assertTrue(requests_extended_output("请给我逐日行程"))
        self.assertFalse(requests_extended_output("Hồ Thác Bà có gì đáng xem?"))

    async def test_client_passes_an_explicit_extended_budget_to_provider(self):
        client = LLMClient.__new__(LLMClient)
        provider = _OptionsStreamingOllama()
        client._ollama = provider

        answer = "".join([
            part async for part in client.chat_stream(
                [{"role": "user", "content": "Lịch trình 5 ngày"}],
                num_predict=1536,
            )
        ])

        self.assertEqual(answer, "Câu trả lời đầy đủ")
        self.assertEqual(provider.options["num_predict"], 1536)


if __name__ == "__main__":
    unittest.main()
