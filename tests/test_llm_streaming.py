import unittest

from app.services.llm_client import LLMClient, strip_thinking_stream


async def _chunks(parts):
    for part in parts:
        yield part


class _StreamingOllama:
    async def chat_stream(self, messages):
        yield "Mảnh thứ nhất. "
        yield "Mảnh thứ hai."


class StreamingFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_content_is_yielded_incrementally(self):
        received = []
        async for part in strip_thinking_stream(_chunks(["Xin ", "chào", " bạn"])):
            received.append(part)
        self.assertGreater(len(received), 1)
        self.assertEqual("".join(received), "Xin chào bạn")

    async def test_split_thinking_tags_are_removed(self):
        received = []
        parts = ["<thi", "nk>bí mật", "</thi", "nk>Trả ", "lời"]
        async for part in strip_thinking_stream(_chunks(parts)):
            received.append(part)
        self.assertEqual("".join(received), "Trả lời")

    async def test_unclosed_thinking_is_not_exposed(self):
        received = []
        async for part in strip_thinking_stream(_chunks(["<think>", "bí mật"])):
            received.append(part)
        self.assertEqual(received, [])

    async def test_llm_facade_does_not_buffer_complete_answer(self):
        client = LLMClient.__new__(LLMClient)
        client._ollama = _StreamingOllama()

        received = []
        async for part in client.chat_stream(
            [{"role": "user", "content": "Xin chào"}]
        ):
            received.append(part)

        self.assertEqual(received, ["Mảnh thứ nhất. ", "Mảnh thứ hai."])

    def test_complete_reply_drops_reasoning_before_stray_closing_tag(self):
        reply = "Internal reasoning that must not leak.</think>Final answer"
        self.assertEqual(LLMClient._strip_thinking(reply), "Final answer")


if __name__ == "__main__":
    unittest.main()
