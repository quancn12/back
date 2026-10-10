import unittest

from app.services.document_processor import MAX_CHUNK_CHARS, chunk_text
from app.services.session_store import SessionStore


class HotelRegressionTests(unittest.TestCase):
    def test_chunk_words_and_size_bound(self):
        chunks = chunk_text("alpha beta gamma delta " * 300)
        self.assertTrue(all(len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks))
        self.assertIn("alpha beta gamma delta", chunks[0])

    def test_chunk_keeps_heading(self):
        chunks = chunk_text(
            ("intro " * 230)
            + "\n## Important heading\n"
            + ("ending " * 240)
        )
        self.assertTrue(any("Important heading" in chunk for chunk in chunks))

    def test_sessions_are_isolated_by_hotel(self):
        store = SessionStore()
        first = store.get_or_create("hotel-a", "same-session")
        first.add_turn("user", "private-a")
        second = store.get_or_create("hotel-b", "same-session")
        self.assertIs(
            store.get_or_create("hotel-a", "same-session"),
            first,
        )
        self.assertEqual(second.history, [])


if __name__ == "__main__":
    unittest.main()
