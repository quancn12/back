import unittest

from app.services.llm_client import LLMClient


class _FakeOllama:
    def __init__(self):
        self.calls = []

    async def embed(self, text):
        self.calls.append(("embed", text))
        return [0.0]

    async def chat(self, messages):
        self.calls.append(("chat", messages))
        return "OK"


class LLMWarmupTests(unittest.IsolatedAsyncioTestCase):
    async def test_warms_embedding_and_chat(self):
        client = LLMClient.__new__(LLMClient)
        client._ollama = _FakeOllama()

        await client.warmup()

        self.assertEqual([call[0] for call in client._ollama.calls], ["embed", "chat"])


if __name__ == "__main__":
    unittest.main()
