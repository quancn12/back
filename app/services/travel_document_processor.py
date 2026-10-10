"""Extract, clean, classify, and chunk VietSage travel-program PDFs."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


CHUNKER_VERSION = "travel-tour-v2-policy"
MAX_CHUNK_CHARS = 1200
OVERLAP_CHARS = 180


@dataclass(frozen=True)
class TravelChunk:
    text: str
    section: str


@dataclass(frozen=True)
class ProcessedTravelDocument:
    title: str
    tour_code: str | None
    duration_days: float | None
    locations: list[str]
    chunks: list[TravelChunk]


_KNOWN_LOCATIONS = [
    ("Đà Nẵng", ("da nang",)),
    ("Bán đảo Sơn Trà", ("ban dao son tra", "son tra")),
    ("Ngũ Hành Sơn", ("ngu hanh son",)),
    ("Biển Mỹ Khê", ("bien my khe", "my khe")),
    ("Bà Nà Hills", ("ba na hills", "ba na")),
    ("Hải Vân Quan", ("hai van quan",)),
    ("Sông Hàn", ("song han",)),
    ("Hội An", ("hoi an",)),
    ("Huế", ("hue",)),
    ("Mỹ Sơn", ("my son",)),
    ("Hồ Thác Bà", ("ho thac ba",)),
    ("Thủy điện Thác Bà", ("thuy dien thac ba", "nha may thuy dien thac ba")),
    ("Yên Bái", ("yen bai",)),
    ("Hà Nội", ("ha noi",)),
    ("Suối Giàng", ("suoi giang",)),
    ("Tú Lệ", ("tu le",)),
    ("Đèo Khau Phạ", ("deo khau pha", "khau pha")),
    ("Trạm Tấu", ("tram tau",)),
    ("Nghĩa Lộ", ("nghia lo",)),
    ("Mù Cang Chải", ("mu cang chai",)),
    ("La Pán Tẩn", ("la pan tan",)),
    ("Bản Hốc", ("ban hoc",)),
    ("Bản Sà Rèn", ("ban sa ren",)),
    ("Lau Camping", ("lau camping",)),
    ("Đảo Hoa", ("dao hoa",)),
    ("Đảo Xanh", ("dao xanh",)),
    ("Đảo Thiên Đường", ("dao thien duong",)),
    ("Động Thủy Tiên", ("dong thuy tien",)),
    ("Vũ Linh", ("vu linh",)),
    ("An Bình", ("an binh village", "an binh")),
]

_HEADER_PREFIXES = (
    "công ty tnhh",
    "địa chỉ:",
    "đc:",
    "vpgd",
    "đt:",
    "website:",
    "fb:",
)

_STOP_HEADINGS = (
    "CÁC QUY ĐỊNH ÁP DỤNG CHO CHƯƠNG TRÌNH",
    "THÔNG TIN QUAN TRỌNG CẦN LƯU Ý",
)

_POLICY_HEADINGS = _STOP_HEADINGS

_SECTION_HEADINGS = (
    "CHƯƠNG TRÌNH DU LỊCH",
    "LỊCH TRÌNH CHI TIẾT",
    "GIÁ TOUR BAO GỒM",
    "GIÁ TOUR KHÔNG BAO GỒM",
    "GIÁ TOUR TRẺ EM",
    "LƯU Ý",
)


def normalize_vietnamese(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _extract_tour_code(filename: str, text: str) -> str | None:
    haystack = f"{filename}\n{text[:1500]}"
    match = re.search(r"\b(HVNT|HVTB)\s*(\d{4}-\d{2})?\b", haystack, re.IGNORECASE)
    if not match:
        return None
    prefix = match.group(1).upper()
    suffix = match.group(2)
    return f"{prefix} {suffix}" if suffix else prefix


def _extract_duration_days(filename: str, text: str) -> float | None:
    haystack = f"{filename}\n{text[:2000]}"
    match = re.search(r"(?<!\d)(\d+(?:[,.]\d+)?)\s*N(?:\s*\d+\s*[ĐD])?", haystack, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(",", "."))
    match = re.search(r"Thời\s*gian\s*:\s*(\d+)\s*ngày", haystack, re.IGNORECASE)
    return float(match.group(1)) if match else None


def _extract_locations(filename: str, text: str) -> list[str]:
    normalized = normalize_vietnamese(f"{filename}\n{text}")
    locations = []
    for display_name, aliases in _KNOWN_LOCATIONS:
        if any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases):
            locations.append(display_name)
    return locations


def _is_company_header(line: str) -> bool:
    normalized = line.strip().lower()
    if normalized.startswith(_HEADER_PREFIXES):
        return True
    return "gp lhnd" in normalized or "facebook.com/du lịch hưng việt" in normalized


def _clean_pages(page_texts: list[str]) -> str:
    cleaned_lines: list[str] = []
    is_tour_program = any(
        "CHƯƠNG TRÌNH DU LỊCH" in page_text.upper() for page_text in page_texts
    )
    started = not is_tour_program
    for page_text in page_texts:
        for raw_line in page_text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line or re.fullmatch(r"\d{1,3}", line):
                continue
            if _is_company_header(line):
                continue
            if "CHƯƠNG TRÌNH DU LỊCH" in line.upper():
                started = True
            if not started:
                continue
            # Price placeholders contain no usable amount and must not be stated as facts.
            if "GIÁ TOUR TRỌN GÓI" in line.upper() and not re.search(r"\d[\d.,]{2,}", line):
                continue
            cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_heading = "Tổng quan"
    current_lines: list[str] = []

    for line in text.splitlines():
        upper = line.upper().strip(" :-")
        is_policy_heading = any(upper.startswith(h) for h in _POLICY_HEADINGS)
        matched_heading = next((h for h in _SECTION_HEADINGS if upper.startswith(h)), None)
        numbered_heading = re.match(r"^(\d+(?:\.\d+)*)\.\s+(.{3,100})$", line.strip())
        if matched_heading is None and numbered_heading:
            matched_heading = numbered_heading.group(2).strip()
        if is_policy_heading:
            if current_heading == "Policy":
                current_lines.append(line)
            else:
                if current_lines:
                    sections.append((current_heading, "\n".join(current_lines).strip()))
                current_heading = "Policy"
                # Keep the heading in the policy body; answers quote extracted
                # source wording and must retain its actual section label.
                current_lines = [line]
        elif current_heading == "Policy":
            # Numbered clauses and subsection labels belong to the legal tail;
            # do not reclassify them as ordinary itinerary sections.
            current_lines.append(line)
        elif matched_heading:
            if current_lines:
                sections.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = matched_heading.title()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_heading, "\n".join(current_lines).strip()))
    return [(heading, body) for heading, body in sections if body]


def _split_long_text(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]

    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > MAX_CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                end = min(start + MAX_CHUNK_CHARS, len(paragraph))
                chunks.append(paragraph[start:end].strip())
                if end == len(paragraph):
                    break
                start = max(end - OVERLAP_CHARS, start + 1)
            continue

        candidate = f"{current}\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= MAX_CHUNK_CHARS:
            current = candidate
        else:
            chunks.append(current)
            overlap = current[-OVERLAP_CHARS:].strip()
            current = f"{overlap}\n{paragraph}".strip()

    if current:
        chunks.append(current)
    return chunks


def process_travel_pdf(file_path: str, filename: str | None = None) -> ProcessedTravelDocument:
    path = Path(file_path)
    display_filename = filename or path.name
    reader = PdfReader(str(path))
    page_texts = [(page.extract_text() or "") for page in reader.pages]
    raw_text = "\n".join(page_texts)
    cleaned_text = _clean_pages(page_texts)
    if not cleaned_text:
        raise ValueError("Không trích xuất được nội dung tour hữu ích từ PDF.")

    title = Path(display_filename).stem
    tour_code = _extract_tour_code(display_filename, raw_text)
    duration_days = _extract_duration_days(display_filename, raw_text)
    locations = _extract_locations(display_filename, cleaned_text)
    location_text = ", ".join(locations) if locations else "Chưa phân loại"

    chunks: list[TravelChunk] = []
    for section, body in _split_sections(cleaned_text):
        for part in _split_long_text(body):
            enriched = (
                f"Tên chương trình: {title}\n"
                f"Điểm đến liên quan: {location_text}\n"
                f"Phần: {section}\n\n{part}"
            )
            chunks.append(TravelChunk(text=enriched, section=section))

    return ProcessedTravelDocument(
        title=title,
        tour_code=tour_code,
        duration_days=duration_days,
        locations=locations,
        chunks=chunks,
    )
