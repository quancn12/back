"""
Đọc PDF và chia thành các đoạn (chunk) nhỏ để embed.

Vì data hiện tại của khách sạn CHƯA được gắn nhãn/phân loại, pipeline này
xử lý hoàn toàn tự động dựa trên cấu trúc văn bản (không cần category):
1. Trích xuất toàn bộ text từ PDF.
2. Chia theo đoạn văn (paragraph) trước.
3. Gộp các đoạn ngắn lại / cắt các đoạn quá dài để mỗi chunk có độ dài
   hợp lý cho embedding (đủ ngữ cảnh, không quá dài làm loãng vector).
"""
import re
from pypdf import PdfReader

# Số ký tự tối đa / tối thiểu cho mỗi chunk — có thể tinh chỉnh sau khi
# thấy chất lượng retrieval thực tế.
MAX_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 100
OVERLAP_CHARS = 300  # chồng lấn giữa các chunk để không mất ngữ cảnh ở ranh giới

# Thứ bậc các bộ phân tách để chia nhỏ văn bản từ vĩ mô đến vi mô
MARKDOWN_SEPARATORS = [
    r"\n#{1,6} ",
    r"```\n",
    r"\n\*\*.+\*\*\n",
    r"\n---+",
    r"\n___+",
    r"\n\n",
    r"\n",
    r" ",
    r"",
]


def extract_text_from_pdf(file_path: str) -> str:
    """Trích xuất toàn bộ text thô từ file PDF."""
    reader = PdfReader(file_path)
    pages_text = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages_text.append(text)
    return "\n\n".join(pages_text)


def _split_text(text: str, separators: list[str]) -> list[str]:
    """Chia nhỏ văn bản đệ quy sử dụng danh sách dấu phân tách thứ bậc."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]

    # Tìm dấu phân tách phù hợp nhất xuất hiện trong văn bản
    separator = separators[-1]
    new_separators = []
    for i, sep in enumerate(separators):
        if sep == "":
            separator = sep
            break
        if re.search(sep, text):
            separator = sep
            new_separators = separators[i + 1:]
            break

    # Giữ lại separator để không làm dính từ hoặc mất tiêu đề Markdown.
    if separator:
        splits = []
        cursor = 0
        for match in re.finditer(separator, text):
            splits.append(text[cursor:match.end()])
            cursor = match.end()
        splits.append(text[cursor:])
    else:
        splits = list(text)

    # Loại bỏ khoảng trắng thừa
    good_splits = [s for s in splits if s.strip()]

    final_chunks = []
    current_chunk = []
    current_length = 0

    for s in good_splits:
        if len(s) > MAX_CHUNK_CHARS:
            # Ghi nhận chunk hiện tại nếu có trước khi xử lý phần tử quá dài
            if current_chunk:
                final_chunks.append("".join(current_chunk).strip())
                current_chunk = []
                current_length = 0

            # Chia nhỏ đệ quy phần tử quá dài
            if new_separators:
                recursed = _split_text(s, new_separators)
                final_chunks.extend(recursed)
            else:
                # Cắt lát ký tự nếu không còn bộ phân tách nào khác
                start = 0
                while start < len(s):
                    end = start + MAX_CHUNK_CHARS
                    final_chunks.append(s[start:end])
                    start = end - OVERLAP_CHARS
        else:
            if current_length + len(s) <= MAX_CHUNK_CHARS:
                current_chunk.append(s)
                current_length += len(s)
            else:
                # Đóng gói chunk hiện tại
                if current_chunk:
                    final_chunks.append("".join(current_chunk).strip())

                # Tạo gối đầu (overlap) từ phần cuối của chunk trước đó
                overlap_text = ""
                if current_chunk:
                    prev_text = "".join(current_chunk)
                    overlap_size = min(OVERLAP_CHARS, MAX_CHUNK_CHARS - len(s))
                    overlap_text = prev_text[-overlap_size:] if overlap_size > 0 else ""

                current_chunk = []
                current_length = 0
                if overlap_text:
                    current_chunk.append(overlap_text)
                    current_length += len(overlap_text)

                current_chunk.append(s)
                current_length += len(s)

    if current_chunk:
        final_chunks.append("".join(current_chunk).strip())

    return final_chunks


def chunk_text(text: str) -> list[str]:
    """
    Chia text thành các chunk có độ dài hợp lý bằng cách sử dụng các dấu phân tách thứ bậc.
    Gộp các đoạn quá ngắn vào đoạn trước để tránh vụn dữ liệu.
    """
    raw_chunks = _split_text(text, MARKDOWN_SEPARATORS)

    chunks: list[str] = []
    for rc in raw_chunks:
        rc = rc.strip()
        if not rc:
            continue
        # Gộp các chunk quá ngắn vào chunk trước đó nếu có thể
        if (
            len(rc) < MIN_CHUNK_CHARS
            and chunks
            and len(chunks[-1]) + 2 + len(rc) <= MAX_CHUNK_CHARS
        ):
            chunks[-1] = (chunks[-1] + "\n\n" + rc).strip()
        else:
            chunks.append(rc)

    return chunks


def process_pdf(file_path: str) -> list[str]:
    """Hàm tổng hợp: đọc PDF -> trả về danh sách chunk sẵn sàng để embed."""
    raw_text = extract_text_from_pdf(file_path)
    return chunk_text(raw_text)
