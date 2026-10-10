import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import destinations, health, travel_documents
from app.core.security import require_admin, require_configured_admin

ROOT = Path(__file__).resolve().parents[1]


class AdminSecurityTests(unittest.TestCase):
    def test_key_is_required_when_configured(self):
        with patch("app.core.security.settings.admin_api_key", "secret"):
            with self.assertRaises(HTTPException) as raised:
                require_admin(None)
        self.assertEqual(raised.exception.status_code, 401)

    def test_matching_key_is_accepted(self):
        with patch("app.core.security.settings.admin_api_key", "secret"):
            self.assertIsNone(require_admin("secret"))

    def test_wrong_key_is_refused(self):
        with patch("app.core.security.settings.admin_api_key", "secret"):
            for guess in ("", "secre", "secret ", "SECRET", "secretx"):
                with self.subTest(guess=guess):
                    with self.assertRaises(HTTPException) as raised:
                        require_admin(guess)
                    self.assertEqual(raised.exception.status_code, 401)

    def test_empty_key_now_fails_closed_instead_of_opening_the_door(self):
        # This used to return None -- "local mode" -- which, because setup
        # copies a blank key from .env.example, meant every default install
        # served document upload and deletion to anyone.
        with patch("app.core.security.settings.admin_api_key", ""):
            with self.assertRaises(HTTPException) as raised:
                require_admin(None)
        self.assertEqual(raised.exception.status_code, 503)
        # The operator is told which setting to fix, not left guessing.
        self.assertIn("ADMIN_API_KEY", raised.exception.detail)

    def test_empty_key_cannot_be_satisfied_by_sending_an_empty_header(self):
        with patch("app.core.security.settings.admin_api_key", ""):
            with self.assertRaises(HTTPException) as raised:
                require_admin("")
        self.assertEqual(raised.exception.status_code, 503)

    def test_raw_diagnostics_fail_closed_without_a_configured_key(self):
        with patch("app.core.security.settings.admin_api_key", ""):
            with self.assertRaises(HTTPException) as raised:
                require_configured_admin(None)
        self.assertEqual(raised.exception.status_code, 404)

    def test_raw_diagnostics_require_the_configured_key(self):
        with patch("app.core.security.settings.admin_api_key", "secret"):
            with self.assertRaises(HTTPException) as raised:
                require_configured_admin(None)
            self.assertIsNone(require_configured_admin("secret"))
        self.assertEqual(raised.exception.status_code, 401)


class AdminRouteTests(unittest.TestCase):
    """The guard is only worth something if the real routes actually use it."""

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(travel_documents.router)
        return TestClient(app)

    def test_document_delete_is_refused_on_a_default_install(self):
        with patch("app.core.security.settings.admin_api_key", ""):
            response = self._client().delete("/api/documents/anything")
        self.assertEqual(response.status_code, 503)

    def test_document_upload_is_refused_on_a_default_install(self):
        with patch("app.core.security.settings.admin_api_key", ""):
            response = self._client().post(
                "/api/destinations/ha-noi/documents",
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            )
        self.assertEqual(response.status_code, 503)

    def test_document_delete_needs_the_right_key_once_configured(self):
        client = self._client()
        with patch("app.core.security.settings.admin_api_key", "secret"):
            self.assertEqual(client.delete("/api/documents/x").status_code, 401)
            self.assertEqual(
                client.delete(
                    "/api/documents/x", headers={"X-Admin-Key": "wrong"}
                ).status_code,
                401,
            )


class DestinationWriteTests(unittest.TestCase):
    def test_creating_a_destination_is_an_admin_action(self):
        # This route had no guard at all, even with a key configured.
        app = FastAPI()
        app.include_router(destinations.router)
        client = TestClient(app)
        payload = {"slug": "x", "name": "X"}
        with patch("app.core.security.settings.admin_api_key", ""):
            self.assertEqual(client.post("/api/destinations", json=payload).status_code, 503)
        with patch("app.core.security.settings.admin_api_key", "secret"):
            self.assertEqual(client.post("/api/destinations", json=payload).status_code, 401)


class InferenceProbeTests(unittest.TestCase):
    """The two health probes run a real model call. The point of guarding them
    is that the GPU is not touched, so the tests assert on the model call, not
    only on the status code."""

    PROBES = (
        ("/api/health/chat-test", {"message": "xin chào"}, "chat"),
        ("/api/health/embed-test", {"text": "xin chào"}, "embed"),
    )

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(health.router)
        return TestClient(app)

    def _patched_model(self):
        return patch.multiple(
            "app.api.health.ollama_client",
            chat=AsyncMock(return_value="ok"),
            embed=AsyncMock(return_value=[0.0] * 8),
        )

    def test_probes_are_hidden_and_never_reach_the_model_without_a_key(self):
        client = self._client()
        with patch("app.core.security.settings.admin_api_key", ""), self._patched_model():
            from app.api.health import ollama_client

            for path, body, method in self.PROBES:
                with self.subTest(path=path):
                    self.assertEqual(client.post(path, json=body).status_code, 404)
                    getattr(ollama_client, method).assert_not_awaited()

    def test_a_wrong_key_does_not_reach_the_model_either(self):
        client = self._client()
        with patch("app.core.security.settings.admin_api_key", "secret"), self._patched_model():
            from app.api.health import ollama_client

            for path, body, method in self.PROBES:
                with self.subTest(path=path):
                    response = client.post(path, json=body, headers={"X-Admin-Key": "nope"})
                    self.assertEqual(response.status_code, 401)
                    getattr(ollama_client, method).assert_not_awaited()

    def test_the_operator_can_still_run_them(self):
        client = self._client()
        with patch("app.core.security.settings.admin_api_key", "secret"), self._patched_model():
            from app.api.health import ollama_client

            for path, body, method in self.PROBES:
                with self.subTest(path=path):
                    response = client.post(path, json=body, headers={"X-Admin-Key": "secret"})
                    self.assertEqual(response.status_code, 200, response.text)
                    getattr(ollama_client, method).assert_awaited()

    def test_cheap_status_checks_stay_public(self):
        # "Is it up?" must stay answerable without a key -- for an operator,
        # a load balancer, or the setup script.
        with patch("app.core.security.settings.admin_api_key", ""):
            self.assertEqual(self._client().get("/api/health/live").status_code, 200)


class ShippedConfigurationTests(unittest.TestCase):
    def test_example_env_never_carries_a_real_key(self):
        # Setup generates a key into .env; the committed example must stay
        # blank so no two installs share one.
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        lines = [line for line in example.splitlines() if line.startswith("ADMIN_API_KEY=")]
        self.assertEqual(lines, ["ADMIN_API_KEY="])

    @unittest.skipUnless((ROOT / "scripts" / "bootstrap.ps1").is_file(),
                         "Windows setup script is not part of the backend-only repository")
    def test_setup_generates_a_key_when_it_creates_env(self):
        bootstrap = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8")
        self.assertIn("secrets.token_urlsafe", bootstrap)
        self.assertIn("ADMIN_API_KEY=", bootstrap)


if __name__ == "__main__":
    unittest.main()
