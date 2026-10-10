"""Cross-origin boundary: what ALLOWED_ORIGINS resolves to, and what it lets in.

The bundled PWA is served by the same application, so a tight list costs the
product nothing. These tests pin that down so the wildcard cannot creep back in
as a default.
"""
from __future__ import annotations

import logging
import unittest

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.config import (
    Settings,
    cors_startup_notice,
    origins_are_open,
    parse_allowed_origins,
)


def _app_with_origins(raw: str) -> TestClient:
    """A minimal app wired exactly the way app/main.py wires the real one."""
    app = FastAPI()

    @app.get("/probe")
    def probe() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_allowed_origins(raw),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return TestClient(app)


class ParseAllowedOriginsTests(unittest.TestCase):
    def test_wildcard_is_preserved_when_asked_for_explicitly(self):
        self.assertEqual(parse_allowed_origins("*"), ["*"])
        self.assertEqual(parse_allowed_origins("  *  "), ["*"])

    def test_comma_list_is_split_and_trimmed(self):
        self.assertEqual(
            parse_allowed_origins(" https://a.example , https://b.example "),
            ["https://a.example", "https://b.example"],
        )

    def test_trailing_slash_is_dropped_so_it_matches_the_browser_header(self):
        # Browsers send the Origin header without a trailing slash. A pasted
        # URL usually has one, and the mismatch silently blocks the caller.
        self.assertEqual(
            parse_allowed_origins("https://a.example/"), ["https://a.example"]
        )

    def test_duplicates_collapse_and_order_is_kept(self):
        self.assertEqual(
            parse_allowed_origins("https://a.example,https://a.example/,https://b.example"),
            ["https://a.example", "https://b.example"],
        )

    def test_empty_value_blocks_every_cross_origin_caller(self):
        for raw in ("", "   ", ",", " , , "):
            with self.subTest(raw=raw):
                self.assertEqual(parse_allowed_origins(raw), [])

    def test_a_wildcard_inside_a_list_is_not_treated_as_a_list_entry(self):
        # Only a bare "*" opens the API. "*" mixed with real origins is a
        # mistake, and CORSMiddleware would honour it, so keep it recognisable.
        self.assertTrue(origins_are_open(parse_allowed_origins("https://a.example,*")))


class OriginsAreOpenTests(unittest.TestCase):
    def test_only_the_wildcard_counts_as_open(self):
        self.assertTrue(origins_are_open(["*"]))
        self.assertFalse(origins_are_open([]))
        self.assertFalse(origins_are_open(["https://a.example"]))


class DefaultSettingTests(unittest.TestCase):
    def test_shipped_default_is_not_a_wildcard(self):
        # Constructed without .env so this checks the value in the source, not
        # whatever the machine running the tests happens to have configured.
        default = Settings(_env_file=None).allowed_origins
        self.assertNotEqual(default.strip(), "*")
        self.assertFalse(origins_are_open(parse_allowed_origins(default)))

    def test_shipped_default_still_allows_a_local_front_end(self):
        default = Settings(_env_file=None).allowed_origins
        self.assertIn("http://localhost:8000", parse_allowed_origins(default))


class MiddlewareBehaviourTests(unittest.TestCase):
    def test_listed_origin_is_echoed_back(self):
        client = _app_with_origins("https://good.example")
        response = client.get("/probe", headers={"Origin": "https://good.example"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"), "https://good.example"
        )

    def test_unlisted_origin_gets_no_permission_header(self):
        client = _app_with_origins("https://good.example")
        response = client.get("/probe", headers={"Origin": "https://evil.example"})
        # The request itself still runs; it is the browser that refuses to hand
        # the body back without this header.
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    def test_preflight_from_an_unlisted_origin_is_refused(self):
        client = _app_with_origins("https://good.example")
        response = client.options(
            "/probe",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    def test_empty_configuration_refuses_every_origin(self):
        client = _app_with_origins("")
        response = client.get("/probe", headers={"Origin": "https://anything.example"})
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    def test_same_origin_request_is_unaffected_by_a_tight_list(self):
        # This is why tightening the default is safe: the bundled PWA sends no
        # Origin header, so CORS never applies to it.
        client = _app_with_origins("")
        response = client.get("/probe")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})


class StartupNoticeTests(unittest.TestCase):
    """An operator must be told, at startup, when the API is open."""

    def test_wildcard_warns_outside_production(self):
        notice = cors_startup_notice(["*"], "development")
        self.assertIsNotNone(notice)
        level, message = notice
        self.assertEqual(level, "warning")
        self.assertIn("ALLOWED_ORIGINS", message)

    def test_wildcard_is_an_error_in_production(self):
        notice = cors_startup_notice(["*"], "production")
        self.assertIsNotNone(notice)
        level, message = notice
        self.assertEqual(level, "error")
        self.assertIn("production", message)

    def test_app_env_casing_and_padding_do_not_hide_production(self):
        for app_env in ("Production", " PRODUCTION ", "production"):
            with self.subTest(app_env=app_env):
                self.assertEqual(cors_startup_notice(["*"], app_env)[0], "error")

    def test_empty_list_is_reported_but_not_as_a_problem(self):
        level, message = cors_startup_notice([], "development")
        self.assertEqual(level, "info")
        self.assertIn("blocked", message)

    def test_a_real_list_needs_no_comment(self):
        self.assertIsNone(cors_startup_notice(["https://a.example"], "production"))

    def test_every_level_is_a_real_logger_method(self):
        logger = logging.getLogger("test-cors")
        for origins in (["*"], [], ["https://a.example"]):
            notice = cors_startup_notice(origins, "production")
            if notice is not None:
                self.assertTrue(hasattr(logger, notice[0]), notice[0])


class LiveApplicationTests(unittest.TestCase):
    def test_live_application_is_not_open_by_default(self):
        from app.core.config import settings

        if settings.allowed_origins.strip() == "*":
            self.skipTest("this machine's .env opts into the wildcard")
        self.assertFalse(origins_are_open(parse_allowed_origins(settings.allowed_origins)))

    def test_main_hands_the_parsed_list_to_the_middleware(self):
        import app.main as main_module

        self.assertEqual(
            main_module._origins, parse_allowed_origins(settings_raw_origins())
        )


def settings_raw_origins() -> str:
    from app.core.config import settings

    return settings.allowed_origins


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
