import unittest

from app.api.travel_chat import _sanitize_grounded_reply


class GroundingGuardTests(unittest.TestCase):
    def test_replaces_unsupported_superlative_with_source_excerpt(self):
        chunks = [
            {
                "text": (
                    "Tên chương trình: Đà Nẵng\n"
                    "Phần: Tổng quan\n\n"
                    "Mỹ Khê\nBiển / thư giãn\n1–3 giờ\nTắm biển, bình minh"
                ),
                "metadata": {},
            }
        ]
        reply = (
            "1. **Biển Mỹ Khê** – Một trong những bãi biển đẹp nhất Việt Nam, "
            "phù hợp tắm biển [1]."
        )
        sanitized = _sanitize_grounded_reply(reply, chunks)
        self.assertNotIn("đẹp nhất Việt Nam", sanitized)
        self.assertIn("Tắm biển, bình minh", sanitized)
        self.assertIn("[1]", sanitized)

    def test_keeps_supported_neutral_claim(self):
        chunks = [{"text": "Ngũ Hành Sơn có hang động và chùa.", "metadata": {}}]
        reply = "Ngũ Hành Sơn có hang động và chùa [1]."
        self.assertEqual(_sanitize_grounded_reply(reply, chunks), reply)

    def test_replaces_unsupported_marketing_language(self):
        chunks = [{"text": "Mỹ Khê\nBiển / thư giãn\n1–3 giờ\nTắm biển, bình minh", "metadata": {}}]
        reply = "3. **Biển Mỹ Khê** – Lý tưởng để tận hưởng không khí trong lành [1]."
        sanitized = _sanitize_grounded_reply(reply, chunks)
        self.assertNotIn("Lý tưởng", sanitized)
        self.assertIn("Tắm biển, bình minh", sanitized)


if __name__ == "__main__":
    unittest.main()
