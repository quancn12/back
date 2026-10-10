import json
import unittest

import httpx

from app.services.llm_client import OllamaProvider, OUTPUT_LIMIT_NOTICE


class OutputLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_and_sse_report_length_limit(self):
        for reason in ("stop", "length"):
            def respond(request):
                payload = json.loads(request.content)
                result = {
                    "message": {"content": "A response"},
                    "done": True,
                    "done_reason": reason,
                }
                if payload["stream"]:
                    return httpx.Response(200, text=json.dumps(result) + "\n")
                return httpx.Response(200, json=result)

            provider = OllamaProvider()
            provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            try:
                expected = "A response" + (OUTPUT_LIMIT_NOTICE if reason == "length" else "")
                self.assertEqual(await provider.chat([]), expected)
                self.assertEqual("".join([part async for part in provider.chat_stream([])]), expected)
            finally:
                await provider.shutdown()

    async def test_disconnect_is_not_treated_as_success(self):
        provider = OllamaProvider()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=json.dumps({
                "message": {"content": "Partial response"}, "done": False,
            }) + "\n")
        ))
        try:
            with self.assertRaisesRegex(RuntimeError, "before completion"):
                _ = [part async for part in provider.chat_stream([])]
        finally:
            await provider.shutdown()
