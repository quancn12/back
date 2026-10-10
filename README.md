# VietSage — Backend

Backend của **VietSage**, trợ lý du lịch và lưu trú: chatbot RAG tiếng Việt / Anh /
Trung, kỳ lưu trú khách sạn, API cho website đối tác và kho tri thức tour.
Kho này **chỉ chứa phần backend** (FastAPI, RAG, cơ sở dữ liệu, test), tách từ
repo chính tại commit `43443f4` (24/09/2026). Giao diện PWA, launcher Windows và
tài liệu tour thật của doanh nghiệp không có ở đây.

## Công nghệ

- **Python 3.11**, **FastAPI** + Uvicorn, **SQLAlchemy 2** + **Alembic**
- **SQLite** (máy phát triển) hoặc **PostgreSQL 17 + pgvector** (production, CI)
- **Ollama** chạy tại máy: model chat `qwen2.5:3b`, model embedding `qwen3-embedding:0.6b` (1024 chiều)
- Tìm kiếm lai: vector (pgvector/HNSW hoặc cosine trên SQLite) + từ khóa (SQLite FTS5 / PostgreSQL GIN)

## Cấu trúc

```
app/
  main.py                 Khởi tạo FastAPI, kiểm tra Alembic head lúc khởi động, gắn router
  api/                    Các route HTTP
    unified.py            Chat chính (SSE), hội thoại, kỳ lưu trú, dịch vụ khách sạn, consent, đánh giá
    travel_chat.py        Chat Travel JSON/SSE (/api/chat) — giữ hội thoại bằng conversation_id
    partner.py            API cho website đối tác (khóa API, gọi từ máy chủ)
    auth.py               Phiên khách/tài khoản, CSRF, đăng nhập Google (OIDC)
    travel_documents.py   Tải PDF vào kho tri thức, theo dõi việc nạp
    destinations.py, health.py, operations.py, proactive.py, chat.py, documents.py, hotels.py, rag_test.py
  services/               Nghiệp vụ
    unified_chat.py       Bộ điều phối câu trả lời: phân loại ý định → trả lời soạn sẵn / RAG / lịch trình có cấu trúc / model
    response_registry.py  Phân loại ngữ cảnh câu hỏi (lịch trình, chính sách, giá, sửa lịch trình…)
    conversation_context.py  Bộ nhớ hội thoại (điểm đến, số ngày, ngân sách…) và gợi ý câu tiếp
    travel_vector_store.py   Truy hồi lai vector + từ khóa, cache embedding câu hỏi
    travel_ingestion.py      Hàng đợi nạp tài liệu: tách đoạn, tạo embedding, phiên bản hóa, lease
    travel_document_processor.py  Đọc PDF tour, tách mục (lịch trình, chính sách…)
    travel_policy.py, travel_price.py   Trả lời chính sách hủy/đổi theo nguyên văn, câu hỏi giá trung thực
    generative_ui.py      Lịch trình/kế hoạch có cấu trúc (artifact) và bản vá khi sửa bằng lời
    llm_client.py         Client Ollama (chat, stream, embedding, ngân sách token)
    chat_capacity.py, chat_security.py, chat_metrics.py   Giới hạn tải, chống lộ prompt, số liệu
    stay_access.py, partner_access.py, conversation_access.py, auth.py   Quyền truy cập
    place_cards.py        Thẻ địa điểm từ danh mục (datasets/)
    proactive/            Mô hình nghiên cứu gợi ý chủ động (mặc định tắt)
  models/                 Bảng SQLAlchemy và schema Pydantic
  core/                   Cấu hình (.env), kết nối CSDL, kiểm tra schema, khóa admin
alembic/                  Migration (head: 20260924_0013)
tests/                    Test unittest (379 test)
scripts/                  Nạp dữ liệu, benchmark, eval, sinh tài liệu API
datasets/                 Danh mục địa điểm demo + bộ câu hỏi vàng
data/demo, demo_data/     Dữ liệu demo tự tạo (không phải dữ liệu thật)
docs/human/               API.md (đặc tả toàn bộ API, mẫu là response thật), ARCHITECTURE.md, PARTNER_API.md
Proactive Recommendation Algorithm/   Đặc tả thuật toán gợi ý chủ động
```

## Luồng trả lời chat (tóm tắt)

`POST /api/v1/conversations/{id}/messages/stream` → `unified_chat.build_chat_plan`:

1. Chặn yêu cầu trích xuất prompt/dữ liệu nội bộ; câu chào → trả lời soạn sẵn.
2. Đang mở lịch trình và yêu cầu sửa → model sinh bản vá, kiểm tra theo hợp đồng ID; không hợp lệ thì giữ lịch trình cũ.
3. Yêu cầu lịch trình thiếu điểm đến và số ngày → hỏi lại (không tự chọn điểm đến).
4. Chế độ khách sạn: nhận diện yêu cầu dịch vụ → bản nháp chờ khách xác nhận.
5. Travel: truy hồi lai (vector + từ khóa) theo câu hỏi và bộ nhớ hội thoại.
6. Câu hỏi chính sách → trích nguyên văn điều khoản; câu hỏi giá → trả lời trung thực (chưa có bảng giá chính thức).
7. Yêu cầu lịch trình → artifact có cấu trúc dựng từ tài liệu; còn lại → prompt có khối `GROUNDED_FACTS` gửi Ollama, trả về dạng SSE.

## Chạy thử

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Linux/macOS: .venv/bin/pip
cp .env.example .env                                # đặt AUTH_SECRET, ADMIN_API_KEY nếu cần
ollama pull qwen2.5:3b
ollama pull qwen3-embedding:0.6b
python -m alembic upgrade head
python scripts/ingest_travel_data.py --data-root data/demo/corpus --manifest data/demo/manifest.json
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Tài liệu tương tác: `http://127.0.0.1:8000/docs`; đặc tả đầy đủ: [`docs/human/API.md`](docs/human/API.md).
- Thử chat nhanh: `POST /api/chat` với `{"message": "Hồ Thác Bà có gì chơi?"}`; gửi lại `conversation_id` ở lượt sau để giữ hội thoại.
- Các route trả giao diện (`/`, `/app`, `/chat-ui`, `/operations`, `/app-assets/...`) không dùng được trong kho này vì không có tệp giao diện.

## Test

```bash
python -m unittest discover -s tests     # 379 test
python scripts/run_evals.py              # eval offline (36 ca)
python scripts/run_postgres_integration.py   # cần POSTGRES_TEST_DATABASE_URL tới PostgreSQL có pgvector
```

Kết quả tại thời điểm tách: 370 đạt, 8 bỏ qua có lý do, **1 hỏng đã biết**:
`test_travel_price … (locale='zh-Hans')` — câu "这个行程多少钱？" bị nhánh hỏi lại yêu cầu
mơ hồ trả lời trước nhánh trả lời giá; đang chờ quyết định sửa ở repo chính.

Test bỏ qua là test cần phần không có trong kho này (launcher Windows, trang HTML
vận hành, PDF tour thật); chúng tự chạy lại khi các tệp đó có mặt.
