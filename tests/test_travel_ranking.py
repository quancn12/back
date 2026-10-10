"""Ordering rules for travel retrieval candidates.

The ranking decides which documents the model is allowed to answer from, so a
change here silently changes every answer. These tests pin the properties that
matter rather than a particular formula: semantic closeness decides, keyword
overlap only refines, and the keyword bonus stays bounded.
"""
import unittest

from app.services.travel_vector_store import (
    KEYWORD_NUDGE,
    KEYWORD_NUDGE_CAP,
    TravelVectorStore,
    _document_code,
    _result,
)


def candidate(name, distance, keyword_score, vector_rank=0, text=None, filename=None):
    return _result(
        chunk_id=name,
        document=text if text is not None else f"noi dung cua {name}",
        metadata={"source": name, "filename": filename or name},
        distance=distance,
        keyword_score=keyword_score,
        vector_rank=vector_rank,
    )


class TravelRankingTests(unittest.TestCase):
    def rank(self, candidates, top_k=4, query_text=""):
        return [
            item["id"]
            for item in TravelVectorStore._rank(candidates, top_k, query_text)
        ]

    def test_semantic_match_beats_incidental_keyword_overlap(self):
        """The shape of the Hai Van Quan failure.

        A question about Da Nang shares common Vietnamese syllables with Yen Bai
        tour programmes ("quan" inside "tham quan", "cao" inside "vung cao"), so
        the unrelated documents score higher on keywords while being far away
        semantically. The document that answers the question must still win.
        """
        candidates = [
            candidate("yen-bai", distance=0.7503, keyword_score=6.0, vector_rank=1),
            candidate("da-nang", distance=0.3944, keyword_score=4.0, vector_rank=2),
        ]
        self.assertEqual(self.rank(candidates)[0], "da-nang")

    def test_keyword_still_decides_between_equally_close_chunks(self):
        """Keyword overlap keeps its value where distance cannot separate.

        Naming an exact tour code must keep that programme in front of a sibling
        tour whose text is just as close in embedding space.
        """
        candidates = [
            candidate("khong-co-ma-tour", distance=0.42, keyword_score=2.0, vector_rank=1),
            candidate("dung-ma-tour", distance=0.42, keyword_score=7.0, vector_rank=2),
        ]
        self.assertEqual(self.rank(candidates)[0], "dung-ma-tour")

    def test_keyword_bonus_is_capped(self):
        """No amount of keyword overlap outweighs a clearly closer document.

        A long chunk can match many query tokens by chance. The cap keeps that
        worth at most KEYWORD_NUDGE * KEYWORD_NUDGE_CAP, so a document more than
        that much closer cannot be displaced.
        """
        limit = KEYWORD_NUDGE * KEYWORD_NUDGE_CAP
        candidates = [
            candidate("dai-nhieu-tu-khoa", distance=0.90, keyword_score=99.0, vector_rank=1),
            candidate("gan-hon", distance=0.90 - limit - 0.01, keyword_score=0.0, vector_rank=2),
        ]
        self.assertEqual(self.rank(candidates)[0], "gan-hon")

    def test_keyword_only_fallback_keeps_keyword_order(self):
        """The no-embedding path must behave exactly as before.

        When the embedding model is unavailable every candidate is recorded with
        distance 1.0, so the distance term is constant and the ranking has
        nothing to work with except keyword overlap.
        """
        candidates = [
            candidate("it-tu-khoa", distance=1.0, keyword_score=1.0, vector_rank=1),
            candidate("nhieu-tu-khoa", distance=1.0, keyword_score=4.0, vector_rank=2),
            candidate("vua-phai", distance=1.0, keyword_score=2.0, vector_rank=3),
        ]
        self.assertEqual(
            self.rank(candidates),
            ["nhieu-tu-khoa", "vua-phai", "it-tu-khoa"],
        )

    def test_duplicate_text_is_dropped_and_top_k_respected(self):
        candidates = [
            candidate("ban-sao-xa", distance=0.50, keyword_score=0.0, vector_rank=1, text="Cung mot doan"),
            candidate("ban-sao-gan", distance=0.30, keyword_score=0.0, vector_rank=2, text="Cung mot doan"),
            candidate("khac", distance=0.40, keyword_score=0.0, vector_rank=3, text="Doan khac"),
        ]
        ranked = self.rank(candidates, top_k=2)
        self.assertEqual(ranked, ["ban-sao-gan", "khac"])

    def test_ordering_is_stable_for_identical_scores(self):
        """Equal candidates keep retrieval order, so results do not shuffle."""
        candidates = [
            candidate("thu-hai", distance=0.5, keyword_score=1.0, vector_rank=5),
            candidate("thu-nhat", distance=0.5, keyword_score=1.0, vector_rank=4),
        ]
        self.assertEqual(self.rank(candidates), ["thu-nhat", "thu-hai"])


class TourCodeTests(unittest.TestCase):
    """Naming a programme must answer from that programme."""

    HVTB_0004 = "HVTB 0004-23 1N Yên Bái - Hồ Thác Bà - Đảo Thiên đường.pdf"
    HVTB_0002 = "HVTB 0002-23 1N Yên Bái - Hồ Thác Bà - Đảo hoa.pdf"
    KHONG_MA = "HVNT 2N1Đ HÀ NỘI - HỒ THÁC BÀ - NGHĨA LỘ.pdf"

    def rank(self, candidates, query_text, top_k=2):
        return [
            item["id"]
            for item in TravelVectorStore._rank(candidates, top_k, query_text)
        ]

    def test_code_reads_out_of_the_filename(self):
        self.assertEqual(
            _document_code({"filename": self.HVTB_0004}), "hvtb 0004 23"
        )

    def test_filename_without_a_code_yields_nothing(self):
        self.assertEqual(_document_code({"filename": self.KHONG_MA}), "")

    def test_named_programme_outranks_a_closer_sibling_tour(self):
        """The shape of the HVTB 0004-23 lunch failure.

        The itinerary line naming the lunch stop sits in a chunk of the named
        programme that is further away than several sibling tours, so it fell
        outside the window and the answer came back vague.
        """
        candidates = [
            candidate("sibling-gan", 0.5181, 4.0, 1, filename=self.HVTB_0002),
            candidate("duoc-goi-ten", 0.5685, 4.0, 2, filename=self.HVTB_0004),
        ]
        self.assertEqual(
            self.rank(candidates, "HVTB 0004-23 đoàn ăn trưa ở đâu?")[0],
            "duoc-goi-ten",
        )

    def test_without_a_named_code_distance_still_decides(self):
        candidates = [
            candidate("gan-hon", 0.5181, 4.0, 1, filename=self.HVTB_0002),
            candidate("xa-hon", 0.5685, 4.0, 2, filename=self.HVTB_0004),
        ]
        self.assertEqual(
            self.rank(candidates, "đoàn ăn trưa ở đâu?")[0], "gan-hon"
        )

    def test_a_date_in_the_question_is_not_read_as_a_code(self):
        """Only a code that exists in the library can match.

        The code is taken from the filename and looked for in the question, not
        pattern matched out of the question, so numbers a guest happens to type
        cannot promote an unrelated programme.
        """
        candidates = [
            candidate("gan-hon", 0.40, 0.0, 1, filename=self.HVTB_0002),
            candidate("xa-hon", 0.80, 0.0, 2, filename=self.HVTB_0004),
        ]
        self.assertEqual(
            self.rank(candidates, "tôi đi ngày 2026-09 có tour nào không?")[0],
            "gan-hon",
        )

    def test_a_different_code_does_not_promote_this_programme(self):
        candidates = [
            candidate("gan-hon", 0.40, 0.0, 1, filename=self.HVTB_0002),
            candidate("xa-hon", 0.80, 0.0, 2, filename=self.HVTB_0004),
        ]
        self.assertEqual(
            self.rank(candidates, "HVNT 0001-24 đi những đâu?")[0], "gan-hon"
        )


if __name__ == "__main__":
    unittest.main()
