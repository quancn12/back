import unittest

from app.services.conversation_context import (
    MAX_MEMORY_TURNS,
    build_conversation_memory,
    context_suggestions,
    effective_destination_scope,
    filter_answered_suggestions,
)


class ConversationContextTests(unittest.TestCase):
    def test_long_history_keeps_latest_bounded_constraints(self):
        messages = [
            {
                "role": "user",
                "context_type": "TRAVEL",
                "content": "Tôi muốn đi Huế trong 5 ngày, ngân sách 5 triệu.",
            }
        ]
        messages.extend(
            {
                "role": "user",
                "context_type": "TRAVEL",
                "content": f"Tin nhắn phụ số {index}.",
            }
            for index in range(MAX_MEMORY_TURNS + 4)
        )
        messages.append(
            {
                "role": "user",
                "context_type": "TRAVEL",
                "content": "Đính chính: đổi thành Ninh Bình, 2 ngày, ngân sách 2 triệu.",
            }
        )

        memory = build_conversation_memory(messages, mode="TRAVEL")

        self.assertEqual(memory.destination, "Ninh Bình")
        self.assertEqual(memory.duration, "2 ngày")
        self.assertEqual(memory.budget, "2 triệu")
        self.assertNotIn("Huế", memory.prompt_text())

    def test_later_explicit_correction_wins_and_is_recorded(self):
        memory = build_conversation_memory(
            [
                {
                    "role": "user",
                    "context_type": "TRAVEL",
                    "content": "Đi Đà Nẵng 3 ngày, thích biển.",
                },
                {
                    "role": "user",
                    "context_type": "TRAVEL",
                    "content": "Đính chính: đổi thành Hội An, 2 ngày; không thích biển, ưu tiên văn hóa.",
                },
            ],
            mode="TRAVEL",
        )

        self.assertEqual(memory.destination, "Hội An")
        self.assertEqual(memory.duration, "2 ngày")
        self.assertEqual(memory.preferences["interest"], "văn hóa")
        self.assertEqual(memory.preferences["constraint"], "avoid biển")
        self.assertIn("destination = Hội An", memory.prompt_text())

    def test_mode_isolation_excludes_hotel_turns_from_travel_memory(self):
        memory = build_conversation_memory(
            [
                {
                    "role": "user",
                    "context_type": "HOTEL",
                    "content": "Phòng 305, thêm khăn và Wi-Fi là demo-wifi-2026.",
                },
                {
                    "role": "user",
                    "context_type": "TRAVEL",
                    "content": "Tôi muốn đi Ninh Bình trong 2 ngày.",
                },
            ],
            mode="TRAVEL",
        )

        prompt = memory.prompt_text()
        self.assertIn("Ninh Bình", prompt)
        self.assertNotIn("305", prompt)
        self.assertNotIn("demo-wifi-2026", prompt)

    def test_suggestions_are_bounded_contextual_and_allowlisted(self):
        memory = build_conversation_memory(
            [],
            mode="TRAVEL",
            current_message="Tôi muốn đi Ninh Bình trong 2 ngày, ngân sách 2 triệu, thích thiên nhiên.",
        )

        suggestions = context_suggestions(memory, locale="vi", mode="TRAVEL")

        self.assertLessEqual(len(suggestions), 2)
        self.assertEqual({item["id"] for item in suggestions}, {"context-summary"})
        self.assertTrue(all(set(item) == {"id", "label", "prompt"} for item in suggestions))
        self.assertIn("Ninh Bình", suggestions[0]["prompt"])
        self.assertIn("2 ngày", suggestions[0]["prompt"])
        self.assertIn("2 triệu", suggestions[0]["prompt"])

    def test_assistant_answers_suppress_redundant_suggestions_without_entering_memory(self):
        duration_memory = build_conversation_memory(
            [
                {
                    "role": "user",
                    "context_type": "TRAVEL",
                    "content": "Hồ Thác Bà có gì đặc biệt?",
                },
                {
                    "role": "assistant",
                    "context_type": "TRAVEL",
                    "content": "Lịch trình phù hợp kéo dài 3 ngày 2 đêm.",
                },
            ],
            mode="TRAVEL",
            destination_slug="ho-thac-ba",
        )
        budget_memory = build_conversation_memory(
            [
                {
                    "role": "user",
                    "context_type": "TRAVEL",
                    "content": "Tôi muốn đi Hồ Thác Bà trong 2 ngày.",
                },
                {
                    "role": "assistant",
                    "context_type": "TRAVEL",
                    "content": "Ngân sách tham khảo là 7 triệu cho nhóm 4 người.",
                },
            ],
            mode="TRAVEL",
        )

        duration_suggestions = context_suggestions(
            duration_memory, locale="vi", mode="TRAVEL"
        )
        budget_suggestions = context_suggestions(
            budget_memory, locale="vi", mode="TRAVEL"
        )

        self.assertEqual(duration_memory.answered_fields, {"duration"})
        self.assertEqual(budget_memory.answered_fields, {"budget", "travelers"})
        self.assertNotIn(
            "context-duration", {item["id"] for item in duration_suggestions}
        )
        self.assertNotIn(
            "context-budget", {item["id"] for item in budget_suggestions}
        )
        self.assertNotIn("3 ngày", duration_memory.prompt_text())
        self.assertNotIn("7 triệu", budget_memory.prompt_text())
        self.assertNotIn("4 người", budget_memory.query_hint())

    def test_hotel_never_gets_contextual_travel_suggestions(self):
        memory = build_conversation_memory(
            [], mode="HOTEL", current_message="Tôi muốn đi Ninh Bình trong 2 ngày."
        )
        self.assertEqual(context_suggestions(memory, locale="vi", mode="HOTEL"), [])

    def test_current_reply_removes_the_field_it_just_answered(self):
        suggestions = [
            {
                "id": "context-duration",
                "label": "Thêm thời lượng chuyến đi",
                "prompt": "Đi mấy ngày?",
            },
            {
                "id": "context-destination",
                "label": "Chọn điểm đến",
                "prompt": "Đi đâu?",
            },
        ]

        filtered = filter_answered_suggestions(
            suggestions,
            "Hồ Thác Bà phù hợp với lịch trình 2 ngày 1 đêm.",
        )

        self.assertEqual(
            ["context-destination"], [item["id"] for item in filtered]
        )

    def test_current_chinese_reply_removes_duration_and_budget(self):
        suggestions = [
            {"id": "context-duration", "label": "天数", "prompt": "几天？"},
            {"id": "context-budget", "label": "预算", "prompt": "预算？"},
        ]
        filtered = filter_answered_suggestions(
            suggestions,
            "建议安排3天2晚，预算约3000元。",
        )
        self.assertEqual([], filtered)

    def test_destination_change_starts_a_new_answered_field_epoch(self):
        memory = build_conversation_memory(
            [
                {"role": "user", "context_type": "TRAVEL", "content": "Đi Huế"},
                {"role": "assistant", "context_type": "TRAVEL", "content": "3 days is suitable."},
                {"role": "user", "context_type": "TRAVEL", "content": "Change it to Ninh Binh"},
            ],
            mode="TRAVEL",
        )
        suggestions = context_suggestions(memory, locale="en", mode="TRAVEL")
        self.assertNotIn("duration", memory.answered_fields)
        self.assertIn("context-duration", {item["id"] for item in suggestions})

    def test_corrected_destination_drops_stale_editorial_scope(self):
        self.assertEqual(
            effective_destination_scope("demo-hoi-an", "Hội An"),
            "demo-hoi-an",
        )
        self.assertIsNone(
            effective_destination_scope("demo-hue", "Hội An"),
        )

    def test_english_correction_removes_old_destination_from_query_hint(self):
        memory = build_conversation_memory(
            [
                {
                    "role": "user",
                    "context_type": "TRAVEL",
                    "content": "I want to go Hue for 5 days, budget 5m.",
                },
                {
                    "role": "user",
                    "context_type": "TRAVEL",
                    "content": "Correction: change it to Hoi An, 2 days, budget 2m.",
                },
            ],
            mode="TRAVEL",
        )

        self.assertEqual(memory.destination, "Hoi An")
        self.assertNotIn("Hue", memory.query_hint())
        self.assertNotIn("to go Hue", memory.query_hint())


if __name__ == "__main__":
    unittest.main()
