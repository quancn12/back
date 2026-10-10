"""D1/D4: venue cards under Travel replies, and swapping the catalogue by config.

The cards come from a synthetic demo catalogue whose destinations are
fictional (Hoa Mẫu, Bến Lau, ...), so a guest asking about a real place never
gets synthetic data. The tests pin down the promises that matter: selection is
relevant or empty, synthetic data is labelled, prices never leak, the media
route cannot be walked out of, and a catalogue without licence flags disables
cards instead of being guessed at.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.api import unified as unified_api
from app.core.database import Base, get_db
from app.models import auth, destination, hotel, knowledge, unified  # noqa: F401
from app.services import place_cards

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "datasets" / "demo" / "catalogue.json"


class CatalogueCase(unittest.TestCase):
    def setUp(self):
        place_cards.reset_cache_for_tests()

    def tearDown(self):
        place_cards.reset_cache_for_tests()


class SelectionTests(CatalogueCase):
    def test_a_food_question_about_a_catalogue_destination_gets_food_venues(self):
        cards = place_cards.select_place_cards("Ở Hoa Mẫu có gì ăn ngon?", mode="TRAVEL")
        self.assertTrue(1 <= len(cards) <= 3)
        self.assertTrue(all(card["category"] in {"food", "cafe", "market"} for card in cards))

    def test_rainy_day_question_prefers_indoor_venues(self):
        cards = place_cards.select_place_cards("Trời mưa thì ở Bến Lau đi đâu trong nhà?", mode="TRAVEL")
        self.assertTrue(cards)
        state = place_cards.get_catalogue_state()
        by_id = {venue["id"]: venue for venue in state.venues}
        self.assertTrue(all(by_id[card["id"]].get("indoor") or "indoor" in (by_id[card["id"]].get("tags") or []) or "rainy_day" in (by_id[card["id"]].get("tags") or []) for card in cards))

    def test_a_real_place_never_gets_synthetic_cards(self):
        self.assertEqual(place_cards.select_place_cards("Hà Nội có gì ăn ngon?", mode="TRAVEL"), [])

    def test_hotel_mode_gets_no_cards(self):
        self.assertEqual(place_cards.select_place_cards("Ở Hoa Mẫu có gì ăn ngon?", mode="HOTEL"), [])

    def test_naming_a_destination_without_asking_for_places_gets_nothing(self):
        self.assertEqual(place_cards.select_place_cards("Điều khoản hủy tour Hoa Mẫu thế nào?", mode="TRAVEL"), [])

    def test_cards_are_labelled_synthetic_and_never_carry_a_price(self):
        cards = place_cards.select_place_cards("Ở Hoa Mẫu có gì ăn ngon?", mode="TRAVEL")
        for card in cards:
            self.assertEqual(card["license_type"], "synthetic-demo")
            self.assertNotIn("price", card)
            self.assertNotIn("price", json.dumps(card))

    def test_images_point_at_the_catalogue_media_route(self):
        cards = place_cards.select_place_cards("Ở Hoa Mẫu có gì ăn ngon?", mode="TRAVEL")
        self.assertTrue(all(card["image_url"].startswith("/api/v1/media/catalogue/") for card in cards if card["image_url"]))


class CatalogueSwapTests(CatalogueCase):
    """D4: real data is a config change, and a bad file fails closed."""

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()
        super().tearDown()

    def _write(self, catalogue: dict) -> Path:
        path = self.base / "catalogue.json"
        path.write_text(json.dumps(catalogue, ensure_ascii=False), encoding="utf-8")
        return path

    def test_catalogue_path_switches_the_data_source(self):
        catalogue = json.loads(DEMO.read_text(encoding="utf-8"))
        keep = "demo-ben-lau"
        catalogue["destinations"] = [d for d in catalogue["destinations"] if d["slug"] == keep]
        catalogue["venues"] = [v for v in catalogue["venues"] if v["destination_slug"] == keep]
        path = self._write(catalogue)
        with patch.object(place_cards.settings, "catalogue_path", str(path)):
            self.assertEqual(place_cards.select_place_cards("Ở Hoa Mẫu có gì ăn ngon?", mode="TRAVEL"), [])
            place_cards.reset_cache_for_tests()
            self.assertTrue(place_cards.select_place_cards("Ở Bến Lau có gì ăn ngon?", mode="TRAVEL"))

    def test_a_catalogue_missing_licence_flags_disables_cards(self):
        catalogue = json.loads(DEMO.read_text(encoding="utf-8"))
        del catalogue["venues"][0]["media"][0]["license"]["can_train"]
        path = self._write(catalogue)
        with patch.object(place_cards.settings, "catalogue_path", str(path)):
            self.assertIsNone(place_cards.get_catalogue_state())
            self.assertEqual(place_cards.select_place_cards("Ở Hoa Mẫu có gì ăn ngon?", mode="TRAVEL"), [])

    def test_unreadable_or_missing_file_disables_cards(self):
        (self.base / "broken.json").write_text("{not json", encoding="utf-8")
        for path in (self.base / "broken.json", self.base / "missing.json"):
            with self.subTest(path=path.name), patch.object(place_cards.settings, "catalogue_path", str(path)):
                place_cards.reset_cache_for_tests()
                self.assertIsNone(place_cards.get_catalogue_state())

    def test_the_switch_off_setting_disables_cards(self):
        with patch.object(place_cards.settings, "catalogue_enabled", False):
            self.assertEqual(place_cards.select_place_cards("Ở Hoa Mẫu có gì ăn ngon?", mode="TRAVEL"), [])

    def test_a_media_entry_pointing_outside_the_media_dir_is_not_indexed(self):
        catalogue = json.loads(DEMO.read_text(encoding="utf-8"))
        catalogue["venues"][0]["media"][0]["uri"] = "app/core/config.py"
        path = self._write(catalogue)
        shutil.copytree(DEMO.parent / "media", self.base / "media")
        with patch.object(place_cards.settings, "catalogue_path", str(path)):
            state = place_cards.get_catalogue_state()
            self.assertNotIn("config.py", state.media_index)


class MediaRouteTests(CatalogueCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(unified_api.router)
        return TestClient(app)

    def test_a_catalogue_image_is_served_with_safe_headers(self):
        name = next(iter(place_cards.get_catalogue_state().media_index))
        response = self._client().get(f"/api/v1/media/catalogue/{name}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/svg+xml")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])

    def test_names_outside_the_index_are_not_found(self):
        client = self._client()
        for name in ("unknown.svg", "..%2Fcatalogue.json", "..%2F..%2Fapp%2Fmain.py", "catalogue.json", "venue.png"):
            with self.subTest(name=name):
                self.assertEqual(client.get(f"/api/v1/media/catalogue/{name}").status_code, 404)


class StreamPlacesTests(CatalogueCase):
    def setUp(self):
        super().setUp()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(auth_api.router)
        app.include_router(unified_api.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.client.headers["X-CSRF-Token"] = self.client.get("/api/v1/auth/session").json()["csrf_token"]
        self.conversation = self.client.post("/api/v1/conversations", json={}).json()["id"]

    def tearDown(self):
        self.engine.dispose()
        super().tearDown()

    def _done(self, message: str) -> dict:
        async def fake_stream(*args, **kwargs):
            yield "Gợi ý của mình."

        with patch("app.core.database.SessionLocal", self.Session), patch(
            "app.services.unified_chat.travel_vector_store.query", new=AsyncMock(return_value=[])
        ), patch("app.services.unified_chat.llm_client.chat_stream", new=fake_stream):
            text = self.client.post(
                f"/api/v1/conversations/{self.conversation}/messages/stream",
                json={"message": message, "mode": "TRAVEL"},
            ).text
        block = next(b for b in text.split("\n\n") if b.startswith("event: done"))
        return json.loads(block.split("data: ", 1)[1])

    def test_a_places_question_gets_cards_on_done(self):
        done = self._done("Ở Hoa Mẫu có gì ăn ngon?")
        self.assertTrue(1 <= len(done.get("places", [])) <= 3)

    def test_other_questions_leave_done_unchanged(self):
        self.assertNotIn("places", self._done("Đi Đà Nẵng mấy ngày là hợp lý?"))


if __name__ == "__main__":
    unittest.main()
