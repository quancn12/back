from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import operations
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import auth, destination, hotel, knowledge, unified  # noqa: F401
from app.services import chat_metrics
from app.services.chat_metrics import prune_chat_logs, summarize_chat_telemetry


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tempdir.name)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(operations.router)

        def override_db():
            with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)

    def tearDown(self):
        chat_metrics._close_log_handlers()
        self.engine.dispose()
        self.tempdir.cleanup()

    def _write(self, filename: str, records: list[dict]) -> Path:
        path = self.log_dir / filename
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    def test_operator_summary_is_admin_gated_and_aggregate_only(self):
        now = datetime.now(timezone.utc).isoformat()
        self._write(
            "chat-telemetry.jsonl",
            [
                {
                    "request_id": "private-request-id",
                    "timestamp_utc": now,
                    "route": "unified",
                    "status": "ok",
                    "rag_prepare_ms": 12,
                    "first_content_ms": 40,
                    "total_ms": 90,
                    "response_chars": 100,
                    "source_count": 2,
                }
            ],
        )
        with patch.object(settings, "chat_log_dir", str(self.log_dir)), patch.object(
            settings, "chat_telemetry_enabled", True
        ), patch("app.core.security.settings.admin_api_key", "secret"):
            denied = self.client.get("/api/v1/operations/summary")
            allowed = self.client.get(
                "/api/v1/operations/summary",
                headers={"X-Admin-Key": "secret"},
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        payload = allowed.json()
        self.assertEqual(payload["telemetry"]["records"], 1)
        self.assertEqual(payload["telemetry"]["statuses"], {"ok": 1})
        serialized = json.dumps(payload)
        self.assertNotIn("private-request-id", serialized)
        self.assertNotIn("question_chars", serialized)

    def test_operator_summary_is_hidden_when_no_admin_key_exists(self):
        with patch("app.core.security.settings.admin_api_key", ""):
            response = self.client.get("/api/v1/operations/summary")
        self.assertEqual(response.status_code, 404)

    def test_time_retention_removes_old_and_undated_rows(self):
        now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        recent = {
            "timestamp_utc": (now - timedelta(days=2)).isoformat(),
            "status": "ok",
        }
        old = {
            "timestamp_utc": (now - timedelta(days=31)).isoformat(),
            "status": "ok",
        }
        path = self._write(
            "chat-telemetry.jsonl",
            [old, {"status": "undated"}, recent],
        )

        with patch.object(settings, "chat_log_dir", str(self.log_dir)), patch.object(
            settings, "chat_log_retention_days", 30
        ):
            result = prune_chat_logs(now=now)
            summary = summarize_chat_telemetry()

        self.assertEqual(result, {"files": 1, "kept": 1, "removed": 2})
        self.assertEqual(summary["records"], 1)
        self.assertEqual(summary["statuses"], {"ok": 1})
        self.assertNotIn("undated", path.read_text(encoding="utf-8"))

    def test_active_writer_reapplies_retention_without_restart(self):
        now = datetime.now(timezone.utc)
        old = {
            "timestamp_utc": (now - timedelta(days=31)).isoformat(),
            "status": "old",
        }
        path = self._write("chat-telemetry.jsonl", [old])
        current = {
            "timestamp_utc": now.isoformat(),
            "route": "unified",
            "status": "ok",
        }
        with patch.object(settings, "chat_log_dir", str(self.log_dir)), patch.object(
            settings, "chat_log_retention_days", 30
        ), patch.object(chat_metrics, "_last_retention_check", 0.0), patch.object(
            chat_metrics, "_RETENTION_CHECK_SECONDS", 0.0
        ):
            chat_metrics._write_telemetry(current)

        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["ok"], [record["status"] for record in records])

    @unittest.skipUnless((Path(__file__).resolve().parents[1] / "app" / "static" / "operations" / "index.html").is_file(),
                         "operator web page (front-end) is not part of the backend-only repository")
    def test_operator_page_keeps_key_in_memory_and_has_accessible_status(self):
        page = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "operations"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('type="password"', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn('for="admin-key"', page)
        self.assertIn('@media (max-width: 520px)', page)
        self.assertIn('prefers-reduced-motion', page)
        self.assertNotIn("localStorage", page)
        self.assertNotIn("sessionStorage", page)


if __name__ == "__main__":
    unittest.main()
