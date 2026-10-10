"""Regenerate docs/human/API.md, the VietSage API specification.

The route list, parameters, body rules and the authentication each route needs
come from the FastAPI app itself (its OpenAPI document and each route's
dependency tree). Every request and response example comes from a real call
recorded by scripts/capture_api_examples.py in scripts/api_examples.json.
Only the Vietnamese title, description and notes in ``ROUTES`` are written by
hand; the script stops when a route has no entry, or no recorded example.

The legacy Hotel routes exist only with HOTEL_MODE_ENABLED=true, so the app is
loaded with that flag from a temporary directory (no local .env, no database).

Usage:
  python scripts/capture_api_examples.py --database <copy.db>   # when routes change
  python scripts/export_api_docs.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "human" / "API.md"
EXAMPLES = PROJECT_ROOT / "scripts" / "api_examples.json"
ADMIN_404 = "admin404"

# --- Hand-written: title, description and notes for every route -------------
# (title, description, notes). Notes carry rules and errors worth knowing.
ROUTES: dict[str, tuple[str, str, str]] = {
    # Pages and static files
    "GET /": ("Trang chủ PWA", "Trả về giao diện PWA VietSage (giống `/app`).", ""),
    "GET /app": ("Giao diện PWA", "Trả về giao diện PWA: Khám phá, Chat, Khách sạn, Cài đặt.", ""),
    "GET /chat-ui": ("Giao diện chat cũ", "Giao diện chat Travel cũ; `?hotel=<slug>` mở giao diện Hotel cũ khi `HOTEL_MODE_ENABLED=true`.", ""),
    "GET /hotel-chat": ("Giao diện chat Hotel cũ", "Giao diện chat Hotel cũ.", ""),
    "GET /manifest.webmanifest": ("Web app manifest", "Manifest để cài PWA lên màn hình chính.", ""),
    "GET /sw.js": ("Service worker", "Service worker của PWA (lưu đệm phần khung giao diện).", ""),
    "GET /app-assets/{asset_path}": ("Tệp CSS/JS/ảnh của PWA", "Chỉ phục vụ các tệp trong danh sách cho phép `PWA_ASSET_FILES` (`app/main.py`), kèm `Cache-Control: no-cache`.", "Không có trong `/openapi.json`. Tệp ngoài danh sách → `404`."),
    "GET /operations": ("Bảng điều khiển vận hành", "Trang HTML cho người vận hành; trang tự gọi `/api/v1/operations/summary` bằng khóa admin người vận hành nhập.", "Không có trong `/openapi.json`. Chưa đặt `ADMIN_API_KEY` → `404`."),
    "GET /api/info": ("Thông tin máy chủ", "Chế độ dữ liệu, cờ tính năng và quốc gia mặc định.", "Ở `APP_ENV=production`, `data_mode` luôn `real` và `unified_demo_enabled` luôn `false`."),
    # Health
    "GET /api/health/live": ("Kiểm tra tiến trình còn sống", "Không kiểm tra cơ sở dữ liệu hay model; dùng cho bộ cân bằng tải.", ""),
    "GET /api/health/database": ("Kiểm tra cơ sở dữ liệu", "Chạy `SELECT 1` trên cơ sở dữ liệu.", "Không kết nối được → `503`."),
    "GET /api/health/ollama": ("Kiểm tra Ollama", "Ollama có chạy không, đã có model chat và model embedding chưa.", "Không kết nối được Ollama → `503`."),
    "GET /api/health/provider": ("Kiểm tra nhà cung cấp model", "Nhà cung cấp đang dùng (luôn `ollama`) và tình trạng của nó.", "Ollama tắt vẫn trả `200`, với `\"ollama\": {\"ollama_running\": false, \"error\": \"...\"}`."),
    "POST /api/health/chat-test": ("Chạy thử một lượt chat", "Gọi thật model chat một lần, không RAG, không lịch sử.", "Model lỗi → `503`."),
    "POST /api/health/embed-test": ("Chạy thử tạo embedding", "Gọi thật model embedding; trả số chiều và 5 giá trị đầu.", "Model lỗi → `503`."),
    # Accounts
    "GET /api/v1/auth/session": ("Khởi tạo phiên / lấy CSRF", "Gọi đầu tiên khi mở ứng dụng: tạo phiên khách (đặt cookie `vietsage_session`) hoặc đọc phiên hiện có, trả `csrf_token` dùng cho mọi lượt ghi.", ""),
    "POST /api/v1/auth/signup": ("Đăng ký tài khoản", "Tạo tài khoản email + mật khẩu; hội thoại khách hiện tại chuyển sang tài khoản; phiên được đổi mới (cookie + `csrf_token` mới).", "Email đã dùng → `409` (thông báo chung `Account cannot be created.`); mật khẩu dưới 15 ký tự → `422`."),
    "POST /api/v1/auth/login": ("Đăng nhập", "Đăng nhập email + mật khẩu; phiên được đổi mới.", "Sai thông tin → `401 Authentication failed.`; sai nhiều lần liên tiếp → `429`."),
    "POST /api/v1/auth/logout": ("Đăng xuất", "Thu hồi phiên tài khoản, cấp phiên khách mới.", ""),
    "GET /api/v1/auth/me": ("Xem tài khoản hiện tại", "Tài khoản đang đăng nhập và `csrf_token`.", "Chưa đăng nhập → `401`."),
    "GET /api/v1/auth/google/start": ("Bắt đầu đăng nhập Google", "Chuyển trình duyệt tới trang đăng nhập Google (OIDC); đặt cookie `vietsage_oauth_state` 10 phút.", "Chưa cấu hình `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` → `503`."),
    "GET /api/v1/auth/google/callback": ("Google gọi về sau đăng nhập", "Google chuyển khách về đây; thành công → `/app?auth=success`, khách hủy → `/app?auth_error=GOOGLE_CANCELLED`.", "State hoặc ID token sai → `400`; danh tính xung đột → `409`."),
    # Unified: conversations, chat, consent, ratings
    "POST /api/v1/conversations": ("Tạo hội thoại", "Tạo hội thoại thuộc phiên hiện tại. Có `X-Stay-Token` hợp lệ thì gắn kỳ lưu trú và cho phép `preferred_mode=HOTEL`.", ""),
    "GET /api/v1/conversations/{conversation_id}": ("Xem hội thoại", "Thông tin một hội thoại của phiên hiện tại.", "Hội thoại của người khác hoặc không tồn tại đều trả `404`."),
    "POST /api/v1/conversations/{conversation_id}/messages/stream": ("Gửi tin nhắn và nhận trả lời (SSE)", "Câu trả lời chảy dần dạng Server-Sent Events. `TRAVEL`: du lịch (RAG); `HOTEL`: dịch vụ khách sạn, cần `X-Stay-Token`.", "Bỏ `destination_slug` thì giữ phạm vi cũ, gửi `null` thì bỏ phạm vi. Gửi `currentArtifact` để sửa lịch trình đang mở. `HOTEL` không có token lưu trú → `403`. Đủ số lượt chat cùng lúc → `503` + `Retry-After`. Các loại sự kiện: xem mục 1.2."),
    "GET /api/v1/conversations/{conversation_id}/consents": ("Xem trạng thái đồng ý", "Trạng thái đồng ý (consent) theo từng mục đích xử lý dữ liệu của hội thoại.", ""),
    "PUT /api/v1/conversations/{conversation_id}/consents": ("Cập nhật đồng ý", "Đồng ý, từ chối hoặc rút lại một mục đích xử lý dữ liệu.", ""),
    "GET /api/v1/feedback/reasons": ("Danh sách lý do đánh giá", "Các lý do cố định cho 👍/👎.", ""),
    "PUT /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback": ("Đánh giá câu trả lời", "Ghi hoặc sửa đánh giá `up`/`down` kèm một lý do cố định (không có ô chữ tự do).", "`message_id` lấy từ sự kiện `done`. Lý do không hợp với rating → `400`; tin không thuộc hội thoại → `404`."),
    "GET /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback": ("Xem đánh giá", "Đánh giá hiện có của một câu trả lời; `null` khi chưa đánh giá.", ""),
    "DELETE /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback": ("Rút lại đánh giá", "Xóa đánh giá của một câu trả lời; không có gì để xóa vẫn `204`.", ""),
    "GET /api/v1/feedback/summary": ("Tổng hợp đánh giá", "Số đánh giá và lý do theo loại (không có nội dung chat).", ""),
    "GET /api/v1/media/catalogue/{name}": ("Ảnh thẻ địa điểm", "Ảnh SVG của thẻ địa điểm, chỉ ảnh có trong danh mục đang nạp (`CATALOGUE_PATH`); kèm `nosniff`, CSP sandbox, cache 1 ngày.", "Tên không khớp mẫu `^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\\.svg$` hoặc không có → `404`."),
    "GET /api/v1/demo/scenarios": ("Mã phòng demo", "Mã kết nối và PIN của các phòng demo.", "Chỉ khi `UNIFIED_DEMO_ENABLED=true` và không phải production; ngược lại `404`."),
    # Unified: stays and hotel services
    "POST /api/v1/stays/connect": ("Kết nối kỳ lưu trú — bước 1", "Đổi mã phòng (mã QR) lấy `connection_id` để nhập PIN.", "Mã sai → `404`; phòng đang bị khóa vì nhập sai PIN nhiều lần → `429`."),
    "POST /api/v1/stays/verify": ("Kết nối kỳ lưu trú — bước 2 (PIN)", "Xác minh PIN, nhận `access_token` dùng làm header `X-Stay-Token` và phạm vi quyền.", "Sai PIN → `401`; quá số lần → `429` (kể cả PIN đúng) tới khi lễ tân mở khóa; `connection_id` hết hạn → `410`; phòng không có kỳ lưu trú → `404`."),
    "GET /api/v1/stays/active": ("Xem kỳ lưu trú hiện tại", "Kỳ lưu trú của token gửi kèm.", "Không gửi token hoặc token hết hạn → `{\"active\": false, \"stay\": null}`."),
    "DELETE /api/v1/stays/active": ("Ngắt kết nối kỳ lưu trú", "Thu hồi token lưu trú của thiết bị này.", "Token thiếu/không hợp lệ → `401`."),
    "GET /api/v1/hotel/services": ("Danh mục dịch vụ khách sạn", "Dịch vụ của khách sạn đang lưu trú (giá, giờ phục vụ, cần xác nhận hay không).", "Cần quyền `hotel:read`; thiếu token → `401`, thiếu quyền → `403`."),
    "GET /api/v1/hotel/nearby": ("Địa điểm quanh khách sạn", "Địa điểm công khai quanh khách sạn đang lưu trú.", "Không có token hợp lệ → `[]`."),
    "POST /api/v1/service-requests": ("Gửi yêu cầu dịch vụ", "Gửi yêu cầu (khăn tắm, dọn phòng, xe sân bay…) tới khách sạn.", "Cần quyền `service:create`. Gửi lại cùng `idempotency_key` trả về yêu cầu cũ, không tạo trùng. Dịch vụ cần xác nhận mà `confirmed=false` → `409`; dịch vụ không thuộc khách sạn → `404`."),
    "GET /api/v1/service-requests": ("Danh sách yêu cầu dịch vụ", "Các yêu cầu dịch vụ của kỳ lưu trú.", "Cần quyền `service:read`."),
    "POST /api/v1/admin/hotels/{hotel_slug}/rooms/{room_number}/stay-pin": ("Lễ tân cấp PIN cho kỳ lưu trú", "Cấp PIN 6 số ngẫu nhiên cho kỳ lưu trú đang diễn ra của phòng; PIN phòng cũ không còn mở kỳ này. `revoke_devices=true` thu hồi các thiết bị đã kết nối.", "PIN chỉ trả về một lần. Không có phòng hoặc kỳ lưu trú đang diễn ra → `404`."),
    "POST /api/v1/admin/hotels/{hotel_slug}/rooms/{room_number}/pin-unlock": ("Lễ tân mở khóa phòng", "Mở khóa phòng bị khóa vì nhập sai PIN: các lần sai trước thôi tính, thử thách đang mở bị đóng.", ""),
    # Partner API
    "POST /api/v1/admin/partners": ("Tạo đối tác và khóa API", "Tạo đối tác; khóa `api_key` chỉ hiện một lần, hệ thống chỉ lưu SHA-256.", "Slug đã có → `409`. Hướng dẫn tích hợp: [PARTNER_API.md](PARTNER_API.md)."),
    "GET /api/v1/admin/partners": ("Danh sách đối tác", "Danh sách đối tác (không bao giờ trả khóa).", ""),
    "POST /api/v1/admin/partners/{slug}/rotate-key": ("Đổi khóa đối tác", "Cấp khóa mới; khóa cũ hết hiệu lực ngay; hội thoại vẫn thuộc đối tác; bật lại đối tác đã bị khóa.", ""),
    "POST /api/v1/admin/partners/{slug}/deactivate": ("Khóa đối tác", "Mọi lượt gọi bằng khóa của đối tác trả `401` cho tới khi đổi khóa.", ""),
    "POST /api/v1/partner/conversations": ("Đối tác tạo hội thoại", "Tạo hội thoại Travel cho một người dùng của đối tác.", ""),
    "POST /api/v1/partner/conversations/{conversation_id}/messages": ("Đối tác gửi tin (JSON một lần)", "Gửi tin và nhận cả câu trả lời một lần; cùng lõi xử lý với PWA, chỉ chế độ Travel.", "Lỗi giữa chừng → `502`; đủ số lượt chat cùng lúc → `503` + `Retry-After`; vượt giới hạn của đối tác → `429`."),
    "POST /api/v1/partner/conversations/{conversation_id}/messages/stream": ("Đối tác gửi tin (SSE)", "Gửi tin và nhận câu trả lời chảy dần, cùng định dạng sự kiện với PWA (mục 1.2).", ""),
    "GET /api/v1/partner/conversations/{conversation_id}/messages": ("Đối tác xem lịch sử", "200 tin gần nhất, cũ → mới.", ""),
    "DELETE /api/v1/partner/conversations/{conversation_id}": ("Đối tác xóa hội thoại", "Xóa hội thoại cùng tin nhắn, đánh giá và consent (khi khách yêu cầu xóa dữ liệu).", ""),
    # Durable Travel chat
    "POST /api/chat": ("Chat Travel (JSON)", "Trả lời một lần. Mỗi câu trả lời có `conversation_id` (bằng `session_id`); gửi lại `conversation_id` (hoặc `session_id`) ở lượt sau để bot nhớ các lượt trước.", "Bot nhớ 4 lượt hỏi-đáp gần nhất; hội thoại hết hạn sau 2 giờ không dùng. Không gửi id, id lạ hoặc đã hết hạn → bắt đầu hội thoại mới với id mới (luôn dùng id trong câu trả lời mới nhất). Gửi cả hai tên với hai giá trị khác nhau → `422`."),
    "POST /api/chat/stream": ("Chat Travel (SSE)", "Trả lời chảy dần: `token`, `replace`, `done` (`conversation_id`, `session_id`), `error`. Giữ hội thoại giống `POST /api/chat`.", "Đủ số lượt chat cùng lúc → `503` + `Retry-After`."),
    "POST /api/rag-test": ("Xem kết quả tìm tài liệu", "Các đoạn tài liệu bước tìm kiếm trả về cho một câu hỏi; không gọi model chat.", "`destination_slug` không có → `404`."),
    # Destinations
    "POST /api/destinations": ("Tạo điểm đến", "Tạo một điểm đến để gắn tài liệu.", "Slug đã có → `409`; `parent_id` không tồn tại → `400`."),
    "GET /api/destinations": ("Danh sách điểm đến", "Danh sách điểm đến, lọc theo quốc gia.", ""),
    "GET /api/destinations/{slug}": ("Xem điểm đến", "Chi tiết một điểm đến.", "Không có → `404`."),
    # Travel documents
    "POST /api/destinations/{slug}/documents": ("Tải PDF lên kho tri thức", "Tải một PDF cho điểm đến và xếp hàng nạp (tách đoạn, tạo embedding) chạy nền.", "`source_key` tùy chọn (mặc định `upload:<slug>:<tên tệp>`); tải lại cùng nguồn tạo phiên bản mới, nội dung trùng thì `changed=false`. Không phải PDF → `400`; không có điểm đến → `404`; PDF không đọc được → `422`. Theo dõi bằng `GET /api/ingestion-jobs/{job_id}`."),
    "GET /api/destinations/{slug}/documents": ("Danh sách tài liệu của điểm đến", "Tài liệu nguồn của điểm đến, mới cập nhật trước.", "`include_deleted=true` để thấy cả tài liệu đã xóa."),
    "GET /api/documents/{document_id}/versions": ("Các phiên bản của tài liệu", "Các phiên bản của một tài liệu, mới nhất trước.", "Không có tài liệu → `404`."),
    "DELETE /api/documents/{document_id}": ("Xóa tài liệu", "Xóa mềm: đánh dấu `deleted` và tắt mọi phiên bản, chatbot không dùng nữa.", "Không có tài liệu → `404`."),
    "GET /api/ingestion-jobs": ("Danh sách việc nạp tài liệu", "Việc nạp mới trước; lọc theo `status` (`pending`, `processing`, `done`, `failed`).", ""),
    "GET /api/ingestion-jobs/{job_id}": ("Xem một việc nạp tài liệu", "Trạng thái, số lần thử, lỗi (nếu có) của một việc nạp.", "Không có → `404`."),
    # Operations
    "GET /api/v1/operations/summary": ("Số liệu vận hành", "Thời gian phản hồi, trạng thái và đánh giá tổng hợp; không có nội dung chat.", ""),
    # Proactive research
    "GET /api/v1/proactive/availability": ("Tính năng gợi ý chủ động có bật không", "Trạng thái tính năng nghiên cứu gợi ý chủ động.", "Mặc định `PROACTIVE_RESEARCH_ENABLED=false`."),
    "POST /api/v1/proactive/sessions": ("Tạo phiên nghiên cứu", "Tạo phiên 8 giờ, trả `token` dùng cho header `X-Research-Token`.", "Tính năng tắt → `404`."),
    "GET /api/v1/proactive/mode": ("Xem chế độ phiên", "Chế độ hiện tại: `OFF`, `SHADOW` hoặc `LIVE`.", ""),
    "PUT /api/v1/proactive/mode": ("Đổi chế độ phiên", "Bật `SHADOW`/`LIVE` cần `consent=true`.", "Thiếu consent hoặc tính năng tắt → `409`; `SHADOW` chưa bật → `403`; phiên đổi đồng thời → `409` (thử lại)."),
    "POST /api/v1/proactive/poll": ("Hỏi có nên gợi ý không", "Mô hình quyết định có gửi một gợi ý (`OFFER`) hay câu hỏi (`ASK`) lúc này không; `event` là `null` khi không gửi, `status` nêu lý do.", ""),
    "POST /api/v1/proactive/receipt": ("Báo đã hiển thị gợi ý", "Xác nhận gợi ý đã hiện cho khách.", "`status`: `RECEIVED`, `ALREADY_RECEIVED` hoặc `SUPPRESSED`."),
    "POST /api/v1/proactive/reply": ("Gửi câu trả lời của khách", "Câu trả lời của khách cho câu hỏi chủ động (`yes`, `no`, `uncertain`, `abort`); có thể dẫn tới một gợi ý tiếp theo.", "Không có câu hỏi đang chờ → `status = NO_PENDING_QUESTION`."),
    "POST /api/v1/proactive/fixture": ("Nạp tình huống giả lập", "Nạp tình huống D0 để thử (chỉ development, `PROACTIVE_FIXTURE_ENABLED=true`).", ""),
    # Legacy Hotel
    "POST /api/hotels": ("Tạo khách sạn (API cũ)", "Tạo khách sạn trong API Hotel cũ.", "Slug đã có → `400`."),
    "GET /api/hotels": ("Danh sách khách sạn (API cũ)", "Danh sách khách sạn.", ""),
    "GET /api/hotels/{slug}": ("Xem khách sạn (API cũ)", "Chi tiết một khách sạn.", "Không có → `404`."),
    "POST /api/hotels/{slug}/documents": ("Tải tài liệu khách sạn (API cũ)", "Tải PDF và nạp chạy nền vào kho Chroma.", "Không phải PDF → `400`; không có khách sạn → `404`."),
    "GET /api/hotels/{slug}/documents": ("Danh sách tài liệu khách sạn (API cũ)", "Tài liệu đã tải của khách sạn.", ""),
    "POST /api/hotels/{slug}/rag-test": ("Xem kết quả tìm tài liệu khách sạn (API cũ)", "Các đoạn tài liệu khách sạn tìm được cho một câu hỏi.", ""),
    "POST /api/hotels/{slug}/chat": ("Chat Hotel cũ (JSON)", "Trả lời một lần; hội thoại giữ theo `session_id`.", ""),
    "POST /api/hotels/{slug}/chat/stream": ("Chat Hotel cũ (SSE)", "Trả lời chảy dần: `token`, `done` (`session_id`), `error`.", "**Lỗi đã biết:** route này hiện luôn trả `500` vì `started` và `prepared` chưa được khai báo ở `app/api/chat.py:250` (phát hiện khi gọi thật ngày 24/09/2026, chưa sửa)."),
}

PARAM_DOCS = {
    "conversation_id": "ID hội thoại",
    "message_id": "ID câu trả lời, lấy từ `message_id` của sự kiện `done`",
    "slug": "Mã định danh dạng chữ thường-gạch-ngang",
    "hotel_slug": "Slug khách sạn",
    "room_number": "Số phòng",
    "document_id": "ID tài liệu nguồn",
    "job_id": "ID việc nạp tài liệu",
    "name": "Tên tệp ảnh trong danh mục",
    "asset_path": "Đường dẫn tệp, ví dụ `scripts/bootstrap.js`",
    "country_code": "Mã quốc gia ISO 2 chữ",
    "include_deleted": "Có lấy cả tài liệu đã xóa không",
    "offset": "Bỏ qua bao nhiêu bản ghi đầu",
    "limit": "Số bản ghi tối đa",
    "status": "Lọc theo trạng thái",
    "source_key": "Định danh nguồn của tài liệu",
    "context_type": "`TRAVEL` hoặc `HOTEL`",
    "revoke_devices": "Thu hồi luôn các thiết bị đã kết nối",
    "hotel": "Slug khách sạn (giao diện Hotel cũ)",
    "code": "Mã ủy quyền Google trả về",
    "state": "Chuỗi chống giả mạo đã cấp ở bước bắt đầu",
    "error": "Lỗi Google trả về, ví dụ `access_denied` khi khách hủy",
}

GROUPS = [
    ("pages", "Trang và tệp tĩnh"),
    ("auth", "Phiên và tài khoản"),
    ("chat", "Hội thoại, chat, đồng ý, đánh giá"),
    ("stay", "Kỳ lưu trú và dịch vụ khách sạn"),
    ("partner-api", "API cho website đối tác"),
    ("travel-chat", "Chat Travel kiểu cũ"),
    ("destinations", "Điểm đến"),
    ("travel-documents", "Kho tri thức: tài liệu và việc nạp"),
    ("health", "Kiểm tra tình trạng"),
    ("operations", "Vận hành"),
    ("proactive-research", "Gợi ý chủ động (nghiên cứu, mặc định tắt)"),
    ("legacy", "API Hotel cũ (chỉ khi `HOTEL_MODE_ENABLED=true`)"),
]
STAY_PATHS = ("/api/v1/stays", "/api/v1/hotel", "/api/v1/service-requests", "/api/v1/admin/hotels")
STAY_REQUIRED = {"GET /api/v1/hotel/services", "POST /api/v1/service-requests", "GET /api/v1/service-requests", "DELETE /api/v1/stays/active"}
LEGACY_TAGS = {"chat", "documents", "hotels", "rag-test"}
AUTH_BY_DEPENDENCY = {
    "get_current_app_session": "session",
    "require_csrf": "csrf",
    "get_authorized_conversation": "owner",
    "require_admin": "admin503",
    "require_configured_admin": ADMIN_404,
    "require_partner": "partner",
    "require_partner_user": "partner_user",
    "session_row": "research",
}
STATUS_TEXT = {200: "OK", 201: "Created", 202: "Accepted", 204: "No Content", 302: "Found", 303: "See Other",
               400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 409: "Conflict",
               410: "Gone", 422: "Unprocessable Entity", 429: "Too Many Requests", 500: "Internal Server Error",
               502: "Bad Gateway", 503: "Service Unavailable"}
ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
         (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
MAX_ITEMS = 3
MAX_TEXT = 320


def roman(number: int) -> str:
    out = ""
    for value, letters in ROMAN:
        while number >= value:
            out, number = out + letters, number - value
    return out


def _api_routes(routes, route_type):
    """Routes of the app and of every included router (FastAPI 0.13x wraps an
    included router instead of copying its routes; app.py adds no prefix)."""
    for route in routes:
        if isinstance(route, route_type):
            yield route
        elif hasattr(route, "original_router"):
            yield from _api_routes(route.original_router.routes, route_type)


def _dependency_names(dependant) -> set[str]:
    names = set()
    for sub in dependant.dependencies:
        call = getattr(sub, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        names |= _dependency_names(sub)
    return names


def _load_app():
    """OpenAPI document and API routes, with legacy Hotel routes switched on."""
    cwd = os.getcwd()
    # The legacy Chroma store keeps its SQLite file open on Windows.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        os.environ["HOTEL_MODE_ENABLED"] = "true"
        os.environ["CHROMA_PERSIST_DIR"] = str(Path(workdir) / "chroma")
        os.environ["DATABASE_URL"] = f"sqlite:///{(Path(workdir) / 'spec.db').as_posix()}"
        os.environ.setdefault("APP_ENV", "development")
        os.chdir(workdir)  # .env is read from the working directory
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        try:
            from fastapi.routing import APIRoute

            import app.main as main
            return main.app.openapi(), list(_api_routes(main.app.routes, APIRoute))
        finally:
            os.chdir(cwd)


def _schema_type(schema: dict) -> str:
    if not schema:
        return "any"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(dict.fromkeys(_schema_type(part) for part in schema[key]))
    if "const" in schema:
        return f'"{schema["const"]}"'
    if "enum" in schema:
        return " | ".join(f'"{value}"' for value in schema["enum"])
    kind = schema.get("type", "any")
    if kind == "array":
        return f"array<{_schema_type(schema.get('items', {}))}>"
    names = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "object": "object", "null": "null"}
    return names.get(kind, kind)


def _limits(schema: dict) -> list[str]:
    notes = []
    for part in [schema, *schema.get("anyOf", [])]:
        for key, label in (("minLength", "tối thiểu {} ký tự"), ("maxLength", "tối đa {} ký tự"), ("minimum", "≥ {}"),
                           ("maximum", "≤ {}"), ("pattern", "mẫu `{}`")):
            if key in part:
                notes.append(label.format(part[key]))
    return list(dict.fromkeys(notes))


def _default(schema: dict) -> str | None:
    if "default" not in schema:
        return None
    value = schema["default"]
    return "null" if value is None else json.dumps(value, ensure_ascii=False)


def _param_lines(op: dict) -> list[str]:
    """URL and query parameters, in the template's bullet style."""
    lines = []
    for where, label in (("path", "URL Params"), ("query", "Query Parameters")):
        params = [p for p in op.get("parameters", []) if p["in"] == where]
        if not params:
            continue
        items = []
        for p in params:
            schema = p.get("schema", {})
            bits = [_schema_type(schema), "required" if p.get("required") else "optional"]
            if _default(schema) is not None:
                bits.append(f"default: {_default(schema)}")
            text = f"`{p['name']}` *({', '.join(bits)})*"
            if PARAM_DOCS.get(p["name"]):
                text += f": {PARAM_DOCS[p['name']]}."
            items.append(text)
        if len(items) == 1 and where == "path":
            lines.append(f"* **{label}:** {items[0]}")
        else:
            lines.append(f"* **{label}:**")
            lines += [f"  * {item}" for item in items]
    return lines


def _shorten(value):
    if isinstance(value, dict):
        return {k: _shorten(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shorten(v) for v in value[:MAX_ITEMS]]
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "…"
    return value


def _json(value) -> list[str]:
    return ["```json", json.dumps(_shorten(value), ensure_ascii=False, indent=2), "```"]


def _sse_as_json(text: str) -> tuple[str, list[str]]:
    """A recorded event stream as a JSON list of events (middle tokens left out)."""
    events = []
    for block in (b for b in text.strip().split("\n\n") if b.strip()):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append({"event": name, "data": data})
    tokens = [e for e in events if e["event"] == "token"]
    shown = tokens[:3] + [e for e in events if e["event"] != "token"]
    reply = "".join(e["data"].get("text", "") for e in tokens)
    intro = "`Content-Type: text/event-stream` — mỗi phần tử dưới đây là một sự kiện SSE (`event` + `data`)."
    if len(tokens) > 3:
        intro += f" Có {len(tokens)} sự kiện `token`, hiển thị 3 sự kiện đầu; ghép lại được: “{' '.join(reply.split())[:MAX_TEXT]}”."
    # The event list itself is not cut to MAX_ITEMS: `done` must stay visible.
    return intro, ["```json", json.dumps([_shorten(e) for e in shown], ensure_ascii=False, indent=2), "```"]


def _response_section(example: dict) -> list[str]:
    status = example["status"]
    label = f" — {example['label']}" if example.get("label") else ""
    heading = "Response Mẫu" if status < 500 else "Response thực tế"
    lines = [f"#### {heading}{label} (`{status} {STATUS_TEXT.get(status, '')}`)"]
    kind = example["kind"]
    if status == 204:
        lines.append("Không có nội dung.")
    elif kind == "json":
        lines += _json(example["response"])
    elif kind == "sse":
        intro, block = _sse_as_json(example["response"] or "")
        lines += [intro, *block]
    elif kind == "redirect":
        lines += ["```http", f"Location: {example['location']}", "```"]
    elif status >= 500:
        lines.append("Máy chủ trả `Internal Server Error` (xem mô tả).")
    else:
        lines.append(f"Trả về tệp `{example['content_type']}`.")
    return lines


def _permission(kinds: set[str], key: str, op: dict | None) -> str:
    parts = []
    if "partner" in kinds:
        parts.append("Khóa đối tác")
    if kinds & {"admin503", ADMIN_404}:
        parts.append("Khóa admin")
    if "research" in kinds:
        parts.append("Token nghiên cứu")
    if "csrf" in kinds:
        parts.append("Phiên + CSRF")
    elif kinds & {"owner", "session"}:
        parts.append("Phiên")
    if any(p["name"] == "X-Stay-Token" for p in (op or {}).get("parameters", [])):
        parts.append("Stay token" if key in STAY_REQUIRED else "Stay token (tùy chọn)")
    return " + ".join(parts) or "Public"


def _permission_detail(kinds: set[str], key: str, op: dict | None) -> str:
    """The same access rule, naming the headers to send."""
    parts = []
    if "partner" in kinds:
        parts.append("`Authorization: Bearer <khóa đối tác>` và `X-Partner-User: <mã người dùng>`")
    if kinds & {"admin503", ADMIN_404}:
        parts.append("`X-Admin-Key: <ADMIN_API_KEY>`")
    if "research" in kinds:
        parts.append("`X-Research-Token: <token>`")
    if "csrf" in kinds:
        parts.append("cookie phiên và `X-CSRF-Token: <csrf_token>`")
    elif kinds & {"owner", "session"}:
        parts.append("cookie phiên")
    if "owner" in kinds:
        parts.append("hội thoại phải thuộc phiên này")
    if any(p["name"] == "X-Stay-Token" for p in (op or {}).get("parameters", [])):
        parts.append("`X-Stay-Token: <access_token>`" + ("" if key in STAY_REQUIRED else " (tùy chọn)"))
    if key == "GET /operations":
        return "Public (trang tự hỏi khóa admin)."
    return ("Gửi " + ", ".join(parts) + ".") if parts else "Public."


def _group(key: str, tags: set[str]) -> str:
    path = key.split(" ", 1)[1]
    if tags & LEGACY_TAGS or path == "/hotel-chat":
        return "legacy"
    if "unified-mvp" in tags:
        return "stay" if path.startswith(STAY_PATHS) or "demo/scenarios" in path else "chat"
    return next(iter(tags)) if tags else "pages"


HEAD = """# VietSage API Specification

> **Tài liệu đặc tả API của VietSage** — trợ lý du lịch và lưu trú (PWA, chatbot, kỳ lưu trú khách sạn, API đối tác, kho tri thức).
> Request/Response mẫu là kết quả gọi thật (model `qwen2.5:3b`, dữ liệu demo); token và khóa đã che bớt.
> Cập nhật lần cuối: {captured}.

---

## 1. Chuẩn quy ước chung (API Conventions)

### 1.1. Base URL
* **Chạy tại máy (`SETUP_AND_RUN.bat`):** `http://127.0.0.1:8000`
* **Chia sẻ qua ngrok:** `https://<tên-miền>.ngrok-free.dev`
* **Tài liệu tương tác:** `http://127.0.0.1:8000/docs` · **OpenAPI:** `http://127.0.0.1:8000/openapi.json`

### 1.2. Chuẩn phản hồi (Response Format)
VietSage **không bọc** response trong envelope: body là dữ liệu trực tiếp, mã HTTP cho biết thành công hay lỗi.

```json
{conversation_example}
```

Trường hợp lỗi:
```json
{error_example}
```

Lỗi dữ liệu đầu vào (`422`):
```json
{validation_example}
```

Các route chat trả lời chảy dần (`text/event-stream`), gồm các sự kiện:
```json
[
  {{"event": "token", "data": {{"text": "Nối thêm vào câu trả lời"}}}},
  {{"event": "replace", "data": {{"text": "Thay toàn bộ câu trả lời (câu trả lời soạn sẵn)"}}}},
  {{"event": "action", "data": {{"type": "GENERATIVE_UI | SERVICE_REQUEST_DRAFT", "...": "lịch trình có cấu trúc hoặc bản nháp yêu cầu dịch vụ"}}}},
  {{"event": "done", "data": {{"conversation_id": "...", "resolved_context": "TRAVEL", "suggestions": [], "message_id": 130}}}},
  {{"event": "error", "data": {{"detail": "Không thể hoàn tất câu trả lời."}}}}
]
```

### 1.3. Xác thực & Phân quyền (Authentication & Authorization)
* **Phiên (PWA):** cookie `vietsage_session` do máy chủ đặt khi gọi `GET /api/v1/auth/session`. Các lượt ghi gửi thêm:
  ```http
  X-CSRF-Token: <csrf_token>
  ```
* **Kỳ lưu trú khách sạn:** token lấy từ `POST /api/v1/stays/verify`:
  ```http
  X-Stay-Token: <access_token>
  ```
* **Quản trị (lễ tân, kho tri thức, đối tác, số liệu):** khóa đặt ở biến `ADMIN_API_KEY` trong `.env`:
  ```http
  X-Admin-Key: <ADMIN_API_KEY>
  ```
* **Website đối tác:** khóa cấp bởi `POST /api/v1/admin/partners`:
  ```http
  Authorization: Bearer vsk_<khóa đối tác>
  X-Partner-User: <mã người dùng của đối tác>
  ```
* **Phiên nghiên cứu gợi ý chủ động:** `X-Research-Token: <token>` từ `POST /api/v1/proactive/sessions`.
* Không có tài khoản quản trị mặc định. Phòng demo để thử kỳ lưu trú: `GET /api/v1/demo/scenarios` (phòng `305`, `410`, `208`).

---

## 2. Danh mục Endpoints

| STT | Phương thức | Endpoint | Chức năng | Phân quyền |
| :---: | :---: | :--- | :--- | :---: |
{catalog}

---

## 3. Chi tiết API & Response Template mẫu
"""

ERRORS = [
    (400, "BAD_REQUEST", "Dữ liệu sai nghiệp vụ: tệp không phải PDF, lý do đánh giá không hợp với rating, `parent_id` không tồn tại.", "Đọc `detail` và sửa dữ liệu gửi lên."),
    (401, "UNAUTHORIZED", "Thiếu hoặc sai khóa admin, khóa đối tác, stay token, token nghiên cứu; sai mật khẩu hoặc PIN.", "Kiểm tra header xác thực; đăng nhập hoặc kết nối lại kỳ lưu trú."),
    (403, "FORBIDDEN", "Thiếu/sai `X-CSRF-Token`; stay token thiếu quyền; chat `HOTEL` khi chưa xác minh lưu trú.", "Lấy lại `csrf_token` từ `GET /api/v1/auth/session`; xác minh lại kỳ lưu trú."),
    (404, "NOT_FOUND", "Không có tài nguyên hoặc tài nguyên không thuộc phiên/đối tác đang gọi; route quản trị khi chưa đặt `ADMIN_API_KEY`.", "Kiểm tra lại ID, slug và quyền sở hữu."),
    (409, "CONFLICT", "Trùng slug hoặc email; dịch vụ cần xác nhận mà `confirmed=false`; thiếu consent khi bật gợi ý chủ động.", "Đổi giá trị trùng hoặc gửi lại kèm xác nhận."),
    (410, "GONE", "`connection_id` của bước kết nối kỳ lưu trú đã hết hạn.", "Gọi lại `POST /api/v1/stays/connect`."),
    (422, "UNPROCESSABLE_ENTITY", "Thiếu trường bắt buộc, sai kiểu, vượt độ dài; PDF không đọc được.", "Xem `detail[].loc` để biết trường sai."),
    (429, "TOO_MANY_REQUESTS", "Sai PIN hoặc sai mật khẩu quá nhiều lần; đối tác vượt số lượt gọi mỗi phút.", "Chờ theo header `Retry-After`; lễ tân mở khóa phòng bằng `pin-unlock`."),
    (500, "INTERNAL_SERVER_ERROR", "Lỗi máy chủ chưa xử lý (hiện có ở `POST /api/hotels/{slug}/chat/stream`).", "Xem log máy chủ."),
    (502, "BAD_GATEWAY", "Câu trả lời lỗi giữa chừng ở `POST /api/v1/partner/conversations/{id}/messages`.", "Gửi lại."),
    (503, "SERVICE_UNAVAILABLE", "Đủ số lượt chat cùng lúc (kèm `Retry-After`); Ollama hoặc cơ sở dữ liệu không kết nối được; route quản trị ghi khi chưa đặt `ADMIN_API_KEY`.", "Chờ `Retry-After`; kiểm tra `GET /api/health/ollama`, `GET /api/health/database`."),
]


def build() -> str:
    spec, routes = _load_app()
    examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    entries = []
    for route in routes:
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            path = route.path.replace("{asset_path:path}", "{asset_path}")
            key = f"{method} {path}"
            op = spec["paths"].get(route.path, {}).get(method.lower())
            kinds = {AUTH_BY_DEPENDENCY[n] for n in _dependency_names(route.dependant) if n in AUTH_BY_DEPENDENCY}
            entries.append((_group(key, set(route.tags or [])), path, method, key, op, kinds))
    keys = {e[3] for e in entries}
    problems = [f"no ROUTES entry: {k}" for k in sorted(keys - set(ROUTES))]
    problems += [f"stale ROUTES entry: {k}" for k in sorted(set(ROUTES) - keys)]
    problems += [f"no recorded example: {k}" for k in sorted(keys - set(examples))]
    if problems:
        raise SystemExit("API.md cannot be generated:\n  " + "\n  ".join(problems))

    # Groups in GROUPS order; inside a group, the order of ROUTES (the order a
    # client uses them), not the alphabet.
    order = {key: index for index, (key, _) in enumerate(GROUPS)}
    position = {key: index for index, key in enumerate(ROUTES)}
    entries.sort(key=lambda e: (order[e[0]], position[e[3]]))
    catalog, body = [], []
    for number, (_, path, method, key, op, kinds) in enumerate(entries, start=1):
        label, description, notes = ROUTES[key]
        catalog.append(f"| **{roman(number)}** | `{method}` | `{path}` | {label} | {_permission(kinds, key, op)} |")
        body += [f"### {roman(number)}. {label}", f"* **Endpoint:** `{method} {path}`",
                 f"* **Mô tả:** {description}" + (f" {notes}" if notes else ""),
                 f"* **Phân quyền:** {_permission_detail(kinds, key, op)}"]
        body += _param_lines(op or {})
        request = examples[key][0]["request"]
        if (op or {}).get("requestBody"):
            if request.get("json") is not None:
                body += ["", "#### Request Body", *_json(request["json"])]
            elif request.get("multipart"):
                body += ["* **Request Body:** `multipart/form-data`, trường `file` là tệp PDF."]
        elif method in {"POST", "PUT", "PATCH"}:
            body.append("* **Request Body:** Không có.")
        for index, example in enumerate(examples[key]):
            # A later example with its own request (a follow-up turn) shows that request first.
            if index and example["request"].get("json") not in (None, request.get("json")):
                body += ["", f"#### Request Body — {example['label']}", *_json(example["request"]["json"])]
            body += ["", *_response_section(example)]
        body += ["", "---", ""]

    errors = {e["label"]: e for e in examples.get("_errors", [])}
    head = HEAD.format(
        captured="24/09/2026",
        conversation_example=json.dumps(examples["POST /api/v1/conversations"][0]["response"], ensure_ascii=False, indent=2),
        error_example=json.dumps(errors["404"]["response"], ensure_ascii=False, indent=2),
        validation_example=json.dumps(errors["422"]["response"], ensure_ascii=False, indent=2),
        catalog="\n".join(catalog),
    )
    tail = ["## 4. Bảng mã lỗi HTTP (Error Codes)", "",
            "| Mã lỗi HTTP | Error Key | Mô tả nguyên nhân | Hướng xử lý |", "| :---: | :--- | :--- | :--- |"]
    tail += [f"| `{code}` | `{name}` | {cause} | {fix} |" for code, name, cause, fix in ERRORS]
    text = head + "\n" + "\n".join(body) + "\n" + "\n".join(tail)
    return "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    output.write_text(build(), encoding="utf-8", newline="\n")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
