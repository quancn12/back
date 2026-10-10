import unittest

from app.services.response_style import language_signal, prompt_guidance, response_preferences


class ResponseStyleTests(unittest.TestCase):
    def test_explicit_language_overrides_locale_and_keeps_requested_detail(self):
        preferences = response_preferences(
            "Please answer in English, briefly and in a casual tone.",
            "vi",
        )
        self.assertEqual(preferences.locale, "en")
        self.assertTrue(preferences.language_requested)
        self.assertEqual(preferences.detail, "concise")
        self.assertEqual(preferences.tone, "casual")

    def test_current_turn_language_is_used_when_locale_is_stale(self):
        self.assertEqual(response_preferences("What time does breakfast start?", "vi").locale, "en")
        self.assertEqual(response_preferences("你好，请推荐一个地方", "en").locale, "zh-Hans")
        self.assertEqual(response_preferences("Bạn gợi ý lịch trình nào?", "en").locale, "vi")
        self.assertEqual(
            response_preferences("Can I cancel the tour and get a refund?", "vi").locale,
            "en",
        )

    def test_short_followup_can_defer_to_recent_language_signal(self):
        self.assertIsNone(language_signal("And Wi-Fi?"))
        self.assertEqual(language_signal("Please answer in English."), "en")

    def test_default_guidance_is_adaptive_without_fixed_counts(self):
        _, guidance = prompt_guidance("What is Wi-Fi?", "vi")
        self.assertIn("Scale detail to the task", guidance)
        self.assertIn("Do not add a greeting", guidance)
        self.assertIn("Do not assume a fixed duration", guidance)
        self.assertNotIn("2-4 sentences", guidance)
        self.assertNotIn("3-6 bullet", guidance)

    def test_explicit_format_is_preserved_as_a_preference(self):
        preferences = response_preferences("Giải thích ngắn gọn, không dùng gạch đầu dòng.", "vi")
        self.assertEqual(preferences.detail, "concise")
        self.assertEqual(preferences.format_hint, "no_bullets")


if __name__ == "__main__":
    unittest.main()
