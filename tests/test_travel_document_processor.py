import unittest
from pathlib import Path

from app.services.travel_document_processor import (
    MAX_CHUNK_CHARS,
    _clean_pages,
    _extract_duration_days,
    _extract_locations,
    _extract_tour_code,
    _split_long_text,
    normalize_vietnamese,
    process_travel_pdf,
)
from app.services.travel_policy import source_faithful_policy_reply


class TravelDocumentProcessorTests(unittest.TestCase):
    def test_normalize_vietnamese(self):
        self.assertEqual(normalize_vietnamese("Hồ Thác Bà"), "ho thac ba")

    def test_extract_filename_metadata(self):
        filename = "HVNT 0008-24 3N2Đ TÚ LỆ - MÙ CANG CHẢI - HỒ THÁC BÀ.pdf"
        self.assertEqual(_extract_tour_code(filename, ""), "HVNT 0008-24")
        self.assertEqual(_extract_duration_days(filename, ""), 3.0)
        locations = _extract_locations(filename, "")
        self.assertIn("Tú Lệ", locations)
        self.assertIn("Mù Cang Chải", locations)
        self.assertIn("Hồ Thác Bà", locations)

    def test_half_day_duration(self):
        self.assertEqual(_extract_duration_days("HVTB 0001-23 0,5N.pdf", ""), 0.5)

    def test_clean_pages_keeps_policy_tail_and_separates_it_from_itinerary(self):
        pages = [
            "CÔNG TY TNHH THƯƠNG MẠI VÀ DU LỊCH HƯNG VIỆT\n"
            "Địa chỉ: dữ liệu lặp\n"
            "CHƯƠNG TRÌNH DU LỊCH\n"
            "HỒ THÁC BÀ 1 NGÀY\n"
            "LỊCH TRÌNH CHI TIẾT\n"
            "08h00: Khởi hành.\n"
            "CÁC QUY ĐỊNH ÁP DỤNG CHO CHƯƠNG TRÌNH\n"
            "Nội dung pháp lý không cần embedding."
        ]
        cleaned = _clean_pages(pages)
        self.assertIn("08h00: Khởi hành", cleaned)
        self.assertNotIn("CÔNG TY TNHH", cleaned)
        self.assertIn("CÁC QUY ĐỊNH ÁP DỤNG", cleaned)
        self.assertIn("Nội dung pháp lý", cleaned)

    def test_policy_subheadings_stay_inside_the_policy_section(self):
        from app.services.travel_document_processor import _split_sections

        sections = _split_sections(
            "LỊCH TRÌNH CHI TIẾT\n08h00: Khởi hành.\n"
            "CÁC QUY ĐỊNH ÁP DỤNG CHO CHƯƠNG TRÌNH\n"
            "1. QUY ĐỊNH HỦY TOUR\nHủy tour từ 05-07 ngày: phí 20%.\n"
            "2. ĐỔI LỊCH\nĐổi lịch theo thỏa thuận.\n"
            "THÔNG TIN QUAN TRỌNG CẦN LƯU Ý\nĐọc kỹ điều kiện trên biên nhận."
        )
        self.assertEqual([section for section, _ in sections], ["Lịch Trình Chi Tiết", "Policy"])
        policy_text = sections[1][1]
        self.assertIn("1. QUY ĐỊNH HỦY TOUR", policy_text)
        self.assertIn("2. ĐỔI LỊCH", policy_text)
        self.assertIn("THÔNG TIN QUAN TRỌNG CẦN LƯU Ý", policy_text)

    @unittest.skipUnless((Path(__file__).resolve().parents[1] / "data" / "HỒ THÁC BÀ").is_dir(),
                         "real tour PDFs are not published in this repository")
    def test_real_tour_pdf_policy_is_extracted_verbatim_as_policy(self):
        project_root = Path(__file__).resolve().parents[1]
        pdf = project_root / "data" / "HỒ THÁC BÀ" / "HVNT 1N HỒ THÁC BÀ.pdf"
        processed = process_travel_pdf(str(pdf), pdf.name)
        policy_chunks = [chunk for chunk in processed.chunks if chunk.section == "Policy"]
        self.assertTrue(policy_chunks)
        policy_text = "\n".join(chunk.text for chunk in policy_chunks)
        self.assertIn("CÁC QUY ĐỊNH ÁP DỤNG CHO CHƯƠNG TRÌNH", policy_text)
        self.assertIn("QUY ĐỊNH HỦY TOUR", policy_text)
        self.assertIn("phí hủy 20% phí cọc", policy_text)
        self.assertIn("THÔNG TIN QUAN TRỌNG CẦN LƯU Ý", policy_text)
        reply = source_faithful_policy_reply([
            {
                "text": chunk.text,
                "source": pdf.name,
                "metadata": {
                    "filename": pdf.name,
                    "section": chunk.section,
                    "document_id": "fixture-document",
                    "document_version_id": "fixture-version",
                    "chunk_index": index,
                },
            }
            for index, chunk in enumerate(policy_chunks)
        ], question="HVNT 1N HỒ THÁC BÀ hủy tour")
        self.assertIn("phí hủy 20% phí cọc", reply)
        self.assertIn("điều khoản được ghi nguyên văn", reply.lower())

    def test_chunk_size_is_bounded(self):
        chunks = _split_long_text("A" * (MAX_CHUNK_CHARS * 3))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks))

    def test_generic_travel_guide_does_not_require_tour_heading(self):
        pages = [
            "CẨM NANG DU LỊCH ĐÀ NẴNG\n"
            "1. Tổng quan\n"
            "Đà Nẵng có biển Mỹ Khê và bán đảo Sơn Trà.\n"
            "2. Gợi ý tham quan\n"
            "Có thể tham quan trong nửa ngày."
        ]
        cleaned = _clean_pages(pages)
        self.assertIn("CẨM NANG DU LỊCH ĐÀ NẴNG", cleaned)
        self.assertIn("bán đảo Sơn Trà", cleaned)


if __name__ == "__main__":
    unittest.main()
