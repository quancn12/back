import unittest
from unittest.mock import patch

from app.core.config import settings
from app.services.chat_metrics import observe_sse_stream


async def _stream(events):
    for event in events:
        yield event


class ChatTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_telemetry_excludes_chat_content_and_session_data(self):
        events = [
            'event: token\ndata: {"text":"Câu trả lời riêng tư"}\n\n',
            'event: sources\ndata: {"sources":[{"text":"Nguồn riêng tư"}]}\n\n',
            'event: done\ndata: {"conversation_id":"private-id"}\n\n',
        ]
        records = []
        with (
            patch.object(settings, "chat_log_enabled", False),
            patch.object(settings, "chat_telemetry_enabled", True),
            patch("app.services.chat_metrics._write_telemetry", records.append),
            patch("app.services.chat_metrics.current_chat_usage", return_value={
                "eval_count": 42,
                "eval_duration": 1_000_000,
            }),
        ):
            result = [
                event async for event in observe_sse_stream(
                    _stream(events),
                    started=0.0,
                    prepared=0.0,
                    scope="private-scope",
                    route="unified",
                    question="Câu hỏi riêng tư",
                    session_id="private-id",
                    retrieved_chunk_count=1,
                )
            ]

        self.assertEqual(result, events)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["route"], "unified")
        self.assertEqual(record["question_chars"], len("Câu hỏi riêng tư"))
        self.assertEqual(record["response_chars"], len("Câu trả lời riêng tư"))
        self.assertEqual(record["source_count"], 1)
        self.assertEqual(record["provider_usage"]["eval_count"], 42)
        serialized = str(record)
        self.assertNotIn("Câu hỏi riêng tư", serialized)
        self.assertNotIn("Câu trả lời riêng tư", serialized)
        self.assertNotIn("Nguồn riêng tư", serialized)
        self.assertNotIn("private-id", serialized)
        self.assertNotIn("private-scope", serialized)

    async def test_metadata_telemetry_masks_error_detail(self):
        events = ['event: error\ndata: {"detail":"internal-source.pdf failed"}\n\n']
        records = []
        with (
            patch.object(settings, "chat_log_enabled", False),
            patch.object(settings, "chat_telemetry_enabled", True),
            patch("app.services.chat_metrics._write_telemetry", records.append),
        ):
            _ = [
                event async for event in observe_sse_stream(
                    _stream(events),
                    started=0.0,
                    prepared=0.0,
                    scope="scope",
                    route="travel",
                    question="private question",
                    session_id="session",
                )
            ]

        self.assertEqual(records[0]["status"], "error")
        self.assertNotIn("error", records[0])
        self.assertNotIn("internal-source.pdf", str(records[0]))


if __name__ == "__main__":
    unittest.main()
