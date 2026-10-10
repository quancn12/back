# VietSage API Specification

> **Tài liệu đặc tả API của VietSage** — trợ lý du lịch và lưu trú (PWA, chatbot, kỳ lưu trú khách sạn, API đối tác, kho tri thức).
> Request/Response mẫu là kết quả gọi thật (model `qwen2.5:3b`, dữ liệu demo); token và khóa đã che bớt.
> Cập nhật lần cuối: 24/09/2026.

---

## 1. Chuẩn quy ước chung (API Conventions)

### 1.1. Base URL
* **Chạy tại máy (`SETUP_AND_RUN.bat`):** `http://127.0.0.1:8000`
* **Chia sẻ qua ngrok:** `https://<tên-miền>.ngrok-free.dev`
* **Tài liệu tương tác:** `http://127.0.0.1:8000/docs` · **OpenAPI:** `http://127.0.0.1:8000/openapi.json`

### 1.2. Chuẩn phản hồi (Response Format)
VietSage **không bọc** response trong envelope: body là dữ liệu trực tiếp, mã HTTP cho biết thành công hay lỗi.

```json
{
  "id": "4fb8acba-39e3-42cf-82d9-ca83a6e50d8b",
  "country_code": "VN",
  "destination_slug": null,
  "active_stay_id": null,
  "preferred_mode": "TRAVEL",
  "locale": "vi"
}
```

Trường hợp lỗi:
```json
{
  "detail": "Không tìm thấy điểm đến."
}
```

Lỗi dữ liệu đầu vào (`422`):
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": [
        "body",
        "message"
      ],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

Các route chat trả lời chảy dần (`text/event-stream`), gồm các sự kiện:
```json
[
  {"event": "token", "data": {"text": "Nối thêm vào câu trả lời"}},
  {"event": "replace", "data": {"text": "Thay toàn bộ câu trả lời (câu trả lời soạn sẵn)"}},
  {"event": "action", "data": {"type": "GENERATIVE_UI | SERVICE_REQUEST_DRAFT", "...": "lịch trình có cấu trúc hoặc bản nháp yêu cầu dịch vụ"}},
  {"event": "done", "data": {"conversation_id": "...", "resolved_context": "TRAVEL", "suggestions": [], "message_id": 130}},
  {"event": "error", "data": {"detail": "Không thể hoàn tất câu trả lời."}}
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
| **I** | `GET` | `/` | Trang chủ PWA | Public |
| **II** | `GET` | `/app` | Giao diện PWA | Public |
| **III** | `GET` | `/chat-ui` | Giao diện chat cũ | Public |
| **IV** | `GET` | `/manifest.webmanifest` | Web app manifest | Public |
| **V** | `GET` | `/sw.js` | Service worker | Public |
| **VI** | `GET` | `/app-assets/{asset_path}` | Tệp CSS/JS/ảnh của PWA | Public |
| **VII** | `GET` | `/operations` | Bảng điều khiển vận hành | Public |
| **VIII** | `GET` | `/api/info` | Thông tin máy chủ | Public |
| **IX** | `GET` | `/api/v1/auth/session` | Khởi tạo phiên / lấy CSRF | Phiên |
| **X** | `POST` | `/api/v1/auth/signup` | Đăng ký tài khoản | Phiên + CSRF |
| **XI** | `POST` | `/api/v1/auth/login` | Đăng nhập | Phiên + CSRF |
| **XII** | `POST` | `/api/v1/auth/logout` | Đăng xuất | Phiên + CSRF |
| **XIII** | `GET` | `/api/v1/auth/me` | Xem tài khoản hiện tại | Phiên |
| **XIV** | `GET` | `/api/v1/auth/google/start` | Bắt đầu đăng nhập Google | Phiên |
| **XV** | `GET` | `/api/v1/auth/google/callback` | Google gọi về sau đăng nhập | Phiên |
| **XVI** | `POST` | `/api/v1/conversations` | Tạo hội thoại | Phiên + CSRF + Stay token (tùy chọn) |
| **XVII** | `GET` | `/api/v1/conversations/{conversation_id}` | Xem hội thoại | Phiên |
| **XVIII** | `POST` | `/api/v1/conversations/{conversation_id}/messages/stream` | Gửi tin nhắn và nhận trả lời (SSE) | Phiên + CSRF + Stay token (tùy chọn) |
| **XIX** | `GET` | `/api/v1/conversations/{conversation_id}/consents` | Xem trạng thái đồng ý | Phiên |
| **XX** | `PUT` | `/api/v1/conversations/{conversation_id}/consents` | Cập nhật đồng ý | Phiên + CSRF |
| **XXI** | `GET` | `/api/v1/feedback/reasons` | Danh sách lý do đánh giá | Public |
| **XXII** | `PUT` | `/api/v1/conversations/{conversation_id}/messages/{message_id}/feedback` | Đánh giá câu trả lời | Phiên + CSRF |
| **XXIII** | `GET` | `/api/v1/conversations/{conversation_id}/messages/{message_id}/feedback` | Xem đánh giá | Phiên |
| **XXIV** | `DELETE` | `/api/v1/conversations/{conversation_id}/messages/{message_id}/feedback` | Rút lại đánh giá | Phiên + CSRF |
| **XXV** | `GET` | `/api/v1/feedback/summary` | Tổng hợp đánh giá | Khóa admin |
| **XXVI** | `GET` | `/api/v1/media/catalogue/{name}` | Ảnh thẻ địa điểm | Public |
| **XXVII** | `GET` | `/api/v1/demo/scenarios` | Mã phòng demo | Public |
| **XXVIII** | `POST` | `/api/v1/stays/connect` | Kết nối kỳ lưu trú — bước 1 | Phiên + CSRF |
| **XXIX** | `POST` | `/api/v1/stays/verify` | Kết nối kỳ lưu trú — bước 2 (PIN) | Phiên + CSRF |
| **XXX** | `GET` | `/api/v1/stays/active` | Xem kỳ lưu trú hiện tại | Stay token (tùy chọn) |
| **XXXI** | `DELETE` | `/api/v1/stays/active` | Ngắt kết nối kỳ lưu trú | Stay token |
| **XXXII** | `GET` | `/api/v1/hotel/services` | Danh mục dịch vụ khách sạn | Stay token |
| **XXXIII** | `GET` | `/api/v1/hotel/nearby` | Địa điểm quanh khách sạn | Stay token (tùy chọn) |
| **XXXIV** | `POST` | `/api/v1/service-requests` | Gửi yêu cầu dịch vụ | Stay token |
| **XXXV** | `GET` | `/api/v1/service-requests` | Danh sách yêu cầu dịch vụ | Stay token |
| **XXXVI** | `POST` | `/api/v1/admin/hotels/{hotel_slug}/rooms/{room_number}/stay-pin` | Lễ tân cấp PIN cho kỳ lưu trú | Khóa admin |
| **XXXVII** | `POST` | `/api/v1/admin/hotels/{hotel_slug}/rooms/{room_number}/pin-unlock` | Lễ tân mở khóa phòng | Khóa admin |
| **XXXVIII** | `POST` | `/api/v1/admin/partners` | Tạo đối tác và khóa API | Khóa admin |
| **XXXIX** | `GET` | `/api/v1/admin/partners` | Danh sách đối tác | Khóa admin |
| **XL** | `POST` | `/api/v1/admin/partners/{slug}/rotate-key` | Đổi khóa đối tác | Khóa admin |
| **XLI** | `POST` | `/api/v1/admin/partners/{slug}/deactivate` | Khóa đối tác | Khóa admin |
| **XLII** | `POST` | `/api/v1/partner/conversations` | Đối tác tạo hội thoại | Khóa đối tác |
| **XLIII** | `POST` | `/api/v1/partner/conversations/{conversation_id}/messages` | Đối tác gửi tin (JSON một lần) | Khóa đối tác |
| **XLIV** | `POST` | `/api/v1/partner/conversations/{conversation_id}/messages/stream` | Đối tác gửi tin (SSE) | Khóa đối tác |
| **XLV** | `GET` | `/api/v1/partner/conversations/{conversation_id}/messages` | Đối tác xem lịch sử | Khóa đối tác |
| **XLVI** | `DELETE` | `/api/v1/partner/conversations/{conversation_id}` | Đối tác xóa hội thoại | Khóa đối tác |
| **XLVII** | `POST` | `/api/chat` | Chat Travel (JSON) | Public |
| **XLVIII** | `POST` | `/api/chat/stream` | Chat Travel (SSE) | Public |
| **XLIX** | `POST` | `/api/rag-test` | Xem kết quả tìm tài liệu | Khóa admin |
| **L** | `POST` | `/api/destinations` | Tạo điểm đến | Khóa admin |
| **LI** | `GET` | `/api/destinations` | Danh sách điểm đến | Public |
| **LII** | `GET` | `/api/destinations/{slug}` | Xem điểm đến | Public |
| **LIII** | `POST` | `/api/destinations/{slug}/documents` | Tải PDF lên kho tri thức | Khóa admin |
| **LIV** | `GET` | `/api/destinations/{slug}/documents` | Danh sách tài liệu của điểm đến | Public |
| **LV** | `GET` | `/api/documents/{document_id}/versions` | Các phiên bản của tài liệu | Public |
| **LVI** | `DELETE` | `/api/documents/{document_id}` | Xóa tài liệu | Khóa admin |
| **LVII** | `GET` | `/api/ingestion-jobs` | Danh sách việc nạp tài liệu | Public |
| **LVIII** | `GET` | `/api/ingestion-jobs/{job_id}` | Xem một việc nạp tài liệu | Public |
| **LIX** | `GET` | `/api/health/live` | Kiểm tra tiến trình còn sống | Public |
| **LX** | `GET` | `/api/health/database` | Kiểm tra cơ sở dữ liệu | Public |
| **LXI** | `GET` | `/api/health/ollama` | Kiểm tra Ollama | Public |
| **LXII** | `GET` | `/api/health/provider` | Kiểm tra nhà cung cấp model | Public |
| **LXIII** | `POST` | `/api/health/chat-test` | Chạy thử một lượt chat | Khóa admin |
| **LXIV** | `POST` | `/api/health/embed-test` | Chạy thử tạo embedding | Khóa admin |
| **LXV** | `GET` | `/api/v1/operations/summary` | Số liệu vận hành | Khóa admin |
| **LXVI** | `GET` | `/api/v1/proactive/availability` | Tính năng gợi ý chủ động có bật không | Public |
| **LXVII** | `POST` | `/api/v1/proactive/sessions` | Tạo phiên nghiên cứu | Public |
| **LXVIII** | `GET` | `/api/v1/proactive/mode` | Xem chế độ phiên | Token nghiên cứu |
| **LXIX** | `PUT` | `/api/v1/proactive/mode` | Đổi chế độ phiên | Token nghiên cứu |
| **LXX** | `POST` | `/api/v1/proactive/poll` | Hỏi có nên gợi ý không | Token nghiên cứu |
| **LXXI** | `POST` | `/api/v1/proactive/receipt` | Báo đã hiển thị gợi ý | Token nghiên cứu |
| **LXXII** | `POST` | `/api/v1/proactive/reply` | Gửi câu trả lời của khách | Token nghiên cứu |
| **LXXIII** | `POST` | `/api/v1/proactive/fixture` | Nạp tình huống giả lập | Token nghiên cứu |
| **LXXIV** | `GET` | `/hotel-chat` | Giao diện chat Hotel cũ | Public |
| **LXXV** | `POST` | `/api/hotels` | Tạo khách sạn (API cũ) | Khóa admin |
| **LXXVI** | `GET` | `/api/hotels` | Danh sách khách sạn (API cũ) | Public |
| **LXXVII** | `GET` | `/api/hotels/{slug}` | Xem khách sạn (API cũ) | Public |
| **LXXVIII** | `POST` | `/api/hotels/{slug}/documents` | Tải tài liệu khách sạn (API cũ) | Khóa admin |
| **LXXIX** | `GET` | `/api/hotels/{slug}/documents` | Danh sách tài liệu khách sạn (API cũ) | Public |
| **LXXX** | `POST` | `/api/hotels/{slug}/rag-test` | Xem kết quả tìm tài liệu khách sạn (API cũ) | Khóa admin |
| **LXXXI** | `POST` | `/api/hotels/{slug}/chat` | Chat Hotel cũ (JSON) | Public |
| **LXXXII** | `POST` | `/api/hotels/{slug}/chat/stream` | Chat Hotel cũ (SSE) | Public |

---

## 3. Chi tiết API & Response Template mẫu

### I. Trang chủ PWA
* **Endpoint:** `GET /`
* **Mô tả:** Trả về giao diện PWA VietSage (giống `/app`).
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
Trả về tệp `text/html`.

---

### II. Giao diện PWA
* **Endpoint:** `GET /app`
* **Mô tả:** Trả về giao diện PWA: Khám phá, Chat, Khách sạn, Cài đặt.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
Trả về tệp `text/html`.

---

### III. Giao diện chat cũ
* **Endpoint:** `GET /chat-ui`
* **Mô tả:** Giao diện chat Travel cũ; `?hotel=<slug>` mở giao diện Hotel cũ khi `HOTEL_MODE_ENABLED=true`.
* **Phân quyền:** Public.
* **Query Parameters:**
  * `hotel` *(string | null, optional)*: Slug khách sạn (giao diện Hotel cũ).

#### Response Mẫu (`200 OK`)
Trả về tệp `text/html`.

---

### IV. Web app manifest
* **Endpoint:** `GET /manifest.webmanifest`
* **Mô tả:** Manifest để cài PWA lên màn hình chính.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "name": "VietSage - Trợ lý hành trình",
  "short_name": "VietSage",
  "start_url": "/app",
  "display": "standalone",
  "background_color": "#f3f8f6",
  "theme_color": "#145844",
  "lang": "vi",
  "description": "Trợ lý du lịch và lưu trú thống nhất."
}
```

---

### V. Service worker
* **Endpoint:** `GET /sw.js`
* **Mô tả:** Service worker của PWA (lưu đệm phần khung giao diện).
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
Trả về tệp `application/javascript`.

---

### VI. Tệp CSS/JS/ảnh của PWA
* **Endpoint:** `GET /app-assets/{asset_path}`
* **Mô tả:** Chỉ phục vụ các tệp trong danh sách cho phép `PWA_ASSET_FILES` (`app/main.py`), kèm `Cache-Control: no-cache`. Không có trong `/openapi.json`. Tệp ngoài danh sách → `404`.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
Trả về tệp `application/javascript`.

---

### VII. Bảng điều khiển vận hành
* **Endpoint:** `GET /operations`
* **Mô tả:** Trang HTML cho người vận hành; trang tự gọi `/api/v1/operations/summary` bằng khóa admin người vận hành nhập. Không có trong `/openapi.json`. Chưa đặt `ADMIN_API_KEY` → `404`.
* **Phân quyền:** Public (trang tự hỏi khóa admin).

#### Response Mẫu (`200 OK`)
Trả về tệp `text/html`.

---

### VIII. Thông tin máy chủ
* **Endpoint:** `GET /api/info`
* **Mô tả:** Chế độ dữ liệu, cờ tính năng và quốc gia mặc định. Ở `APP_ENV=production`, `data_mode` luôn `real` và `unified_demo_enabled` luôn `false`.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "status": "ok",
  "app": "Chatbot VietSage",
  "data_mode": "real",
  "hotel_mode_enabled": true,
  "unified_demo_enabled": true,
  "default_country": "Việt Nam",
  "message": "Chatbot VietSage đang chạy. Xem /docs để kiểm thử."
}
```

---

### IX. Khởi tạo phiên / lấy CSRF
* **Endpoint:** `GET /api/v1/auth/session`
* **Mô tả:** Gọi đầu tiên khi mở ứng dụng: tạo phiên khách (đặt cookie `vietsage_session`) hoặc đọc phiên hiện có, trả `csrf_token` dùng cho mọi lượt ghi.
* **Phân quyền:** Gửi cookie phiên.

#### Response Mẫu (`200 OK`)
```json
{
  "authenticated": false,
  "user": null,
  "csrf_token": "626b68fdc68c…",
  "capabilities": {
    "password": true,
    "google": true,
    "password_recovery": false
  }
}
```

---

### X. Đăng ký tài khoản
* **Endpoint:** `POST /api/v1/auth/signup`
* **Mô tả:** Tạo tài khoản email + mật khẩu; hội thoại khách hiện tại chuyển sang tài khoản; phiên được đổi mới (cookie + `csrf_token` mới). Email đã dùng → `409` (thông báo chung `Account cannot be created.`); mật khẩu dưới 15 ký tự → `422`.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`.

#### Request Body
```json
{
  "email": "khach.demo@example.com",
  "password": "mat-khau-dai-it-nhat-15",
  "display_name": "Khách Demo"
}
```

#### Response Mẫu (`201 Created`)
```json
{
  "user": {
    "id": "e2284a54-8fda-4c2d-9d76-6da250854437",
    "email": "khach.demo@example.com",
    "display_name": "Khách Demo",
    "is_guest": false,
    "authentication_methods": [
      "password"
    ]
  },
  "csrf_token": "2564e6fc487c…"
}
```

---

### XI. Đăng nhập
* **Endpoint:** `POST /api/v1/auth/login`
* **Mô tả:** Đăng nhập email + mật khẩu; phiên được đổi mới. Sai thông tin → `401 Authentication failed.`; sai nhiều lần liên tiếp → `429`.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`.

#### Request Body
```json
{
  "email": "khach.demo@example.com",
  "password": "mat-khau-dai-it-nhat-15"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "user": {
    "id": "e2284a54-8fda-4c2d-9d76-6da250854437",
    "email": "khach.demo@example.com",
    "display_name": "Khách Demo",
    "is_guest": false,
    "authentication_methods": [
      "password"
    ]
  },
  "csrf_token": "aab15225d51e…"
}
```

---

### XII. Đăng xuất
* **Endpoint:** `POST /api/v1/auth/logout`
* **Mô tả:** Thu hồi phiên tài khoản, cấp phiên khách mới.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`.
* **Request Body:** Không có.

#### Response Mẫu (`200 OK`)
```json
{
  "authenticated": false,
  "user": null,
  "csrf_token": "32e0b7f40472…",
  "capabilities": {
    "password": true,
    "google": true,
    "password_recovery": false
  }
}
```

---

### XIII. Xem tài khoản hiện tại
* **Endpoint:** `GET /api/v1/auth/me`
* **Mô tả:** Tài khoản đang đăng nhập và `csrf_token`. Chưa đăng nhập → `401`.
* **Phân quyền:** Gửi cookie phiên.

#### Response Mẫu (`200 OK`)
```json
{
  "authenticated": true,
  "user": {
    "id": "e2284a54-8fda-4c2d-9d76-6da250854437",
    "email": "khach.demo@example.com",
    "display_name": "Khách Demo",
    "is_guest": false,
    "authentication_methods": [
      "password"
    ]
  },
  "csrf_token": "2564e6fc487c…",
  "capabilities": {
    "password": true,
    "google": true,
    "password_recovery": false
  }
}
```

---

### XIV. Bắt đầu đăng nhập Google
* **Endpoint:** `GET /api/v1/auth/google/start`
* **Mô tả:** Chuyển trình duyệt tới trang đăng nhập Google (OIDC); đặt cookie `vietsage_oauth_state` 10 phút. Chưa cấu hình `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` → `503`.
* **Phân quyền:** Gửi cookie phiên.

#### Response Mẫu (`302 Found`)
```http
Location: https://accounts.google.com/o/oauth2/v2/auth?client_id=vi-du.apps.googleusercontent.com&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fv1%2Fauth%2Fgoogle%2Fcallback&response_type=code&scope=openid+email+profile&state=u-M-4w…&nonce=NfNMka…
```

---

### XV. Google gọi về sau đăng nhập
* **Endpoint:** `GET /api/v1/auth/google/callback`
* **Mô tả:** Google chuyển khách về đây; thành công → `/app?auth=success`, khách hủy → `/app?auth_error=GOOGLE_CANCELLED`. State hoặc ID token sai → `400`; danh tính xung đột → `409`.
* **Phân quyền:** Gửi cookie phiên.
* **Query Parameters:**
  * `code` *(string | null, optional)*: Mã ủy quyền Google trả về.
  * `state` *(string, required)*: Chuỗi chống giả mạo đã cấp ở bước bắt đầu.
  * `error` *(string | null, optional)*: Lỗi Google trả về, ví dụ `access_denied` khi khách hủy.

#### Response Mẫu (`303 See Other`)
```http
Location: /app?auth_error=GOOGLE_CANCELLED
```

---

### XVI. Tạo hội thoại
* **Endpoint:** `POST /api/v1/conversations`
* **Mô tả:** Tạo hội thoại thuộc phiên hiện tại. Có `X-Stay-Token` hợp lệ thì gắn kỳ lưu trú và cho phép `preferred_mode=HOTEL`.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`, `X-Stay-Token: <access_token>` (tùy chọn).

#### Request Body
```json
{
  "country_code": "VN",
  "locale": "vi"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "id": "4fb8acba-39e3-42cf-82d9-ca83a6e50d8b",
  "country_code": "VN",
  "destination_slug": null,
  "active_stay_id": null,
  "preferred_mode": "TRAVEL",
  "locale": "vi"
}
```

---

### XVII. Xem hội thoại
* **Endpoint:** `GET /api/v1/conversations/{conversation_id}`
* **Mô tả:** Thông tin một hội thoại của phiên hiện tại. Hội thoại của người khác hoặc không tồn tại đều trả `404`.
* **Phân quyền:** Gửi cookie phiên, hội thoại phải thuộc phiên này.
* **URL Params:** `conversation_id` *(string, required)*: ID hội thoại.

#### Response Mẫu (`200 OK`)
```json
{
  "id": "4fb8acba-39e3-42cf-82d9-ca83a6e50d8b",
  "country_code": "VN",
  "destination_slug": null,
  "active_stay_id": null,
  "preferred_mode": "TRAVEL",
  "locale": "vi"
}
```

---

### XVIII. Gửi tin nhắn và nhận trả lời (SSE)
* **Endpoint:** `POST /api/v1/conversations/{conversation_id}/messages/stream`
* **Mô tả:** Câu trả lời chảy dần dạng Server-Sent Events. `TRAVEL`: du lịch (RAG); `HOTEL`: dịch vụ khách sạn, cần `X-Stay-Token`. Bỏ `destination_slug` thì giữ phạm vi cũ, gửi `null` thì bỏ phạm vi. Gửi `currentArtifact` để sửa lịch trình đang mở. `HOTEL` không có token lưu trú → `403`. Đủ số lượt chat cùng lúc → `503` + `Retry-After`. Các loại sự kiện: xem mục 1.2.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`, `X-Stay-Token: <access_token>` (tùy chọn).
* **URL Params:** `conversation_id` *(string, required)*: ID hội thoại.

#### Request Body
```json
{
  "message": "Hồ Thác Bà có gì chơi?",
  "mode": "TRAVEL",
  "locale": "vi"
}
```

#### Response Mẫu — TRAVEL (`200 OK`)
`Content-Type: text/event-stream` — mỗi phần tử dưới đây là một sự kiện SSE (`event` + `data`). Có 200 sự kiện `token`, hiển thị 3 sự kiện đầu; ghép lại được: “Hồ Thác Bà có rất nhiều điều để du khách khám phá và thưởng ngoạn. Bạn có thể tham quan Hồ Thác Bà, chiêm ngưỡng khung cảnh đẹp như chốn bồng lai tiên cảnh với diện tích hơn 23.400ha và diện tích mặt nước hơn 19.050 ha trải dài hơn 80 km2. Bạn cũng có thể tham quan các đảo lớn nhỏ, hệ thống hang động đẹp ẩn sâu trong l”.
```json
[
  {
    "event": "token",
    "data": {
      "text": "H"
    }
  },
  {
    "event": "token",
    "data": {
      "text": "ồ"
    }
  },
  {
    "event": "token",
    "data": {
      "text": " Th"
    }
  },
  {
    "event": "done",
    "data": {
      "conversation_id": "4fb8acba-39e3-42cf-82d9-ca83a6e50d8b",
      "resolved_context": "TRAVEL",
      "suggestions": [],
      "message_id": 130
    }
  }
]
```

#### Request Body — HOTEL
```json
{
  "message": "Cho tôi thêm khăn tắm",
  "mode": "HOTEL",
  "locale": "vi"
}
```

#### Response Mẫu — HOTEL (`200 OK`)
`Content-Type: text/event-stream` — mỗi phần tử dưới đây là một sự kiện SSE (`event` + `data`).
```json
[
  {
    "event": "replace",
    "data": {
      "text": "Mình đã chuẩn bị yêu cầu “Thêm khăn tắm” (miễn phí). Vui lòng kiểm tra và bấm Xác nhận trước khi gửi tới khách sạn."
    }
  },
  {
    "event": "action",
    "data": {
      "type": "SERVICE_REQUEST_DRAFT",
      "service": {
        "id": "demo-svc_towel",
        "code": "svc_towel",
        "name": "Thêm khăn tắm",
        "price": 0,
        "currency": "VND",
        "estimated_minutes": 15
      },
      "requires_confirmation": true
    }
  },
  {
    "event": "done",
    "data": {
      "conversation_id": "5bc0fffb-1a59-44d0-8eba-256449535fdd",
      "resolved_context": "HOTEL",
      "suggestions": [],
      "message_id": 132
    }
  }
]
```

---

### XIX. Xem trạng thái đồng ý
* **Endpoint:** `GET /api/v1/conversations/{conversation_id}/consents`
* **Mô tả:** Trạng thái đồng ý (consent) theo từng mục đích xử lý dữ liệu của hội thoại.
* **Phân quyền:** Gửi cookie phiên, hội thoại phải thuộc phiên này.
* **URL Params:** `conversation_id` *(string, required)*: ID hội thoại.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "purpose": "PERSONALIZED_RECOMMENDATIONS",
    "status": "GRANTED",
    "policy_version": "demo-1.0",
    "recorded_at": "2026-09-24T16:17:10.723536",
    "withdrawn_at": null
  }
]
```

---

### XX. Cập nhật đồng ý
* **Endpoint:** `PUT /api/v1/conversations/{conversation_id}/consents`
* **Mô tả:** Đồng ý, từ chối hoặc rút lại một mục đích xử lý dữ liệu.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`, hội thoại phải thuộc phiên này.
* **URL Params:** `conversation_id` *(string, required)*: ID hội thoại.

#### Request Body
```json
{
  "purpose": "PERSONALIZED_RECOMMENDATIONS",
  "status": "GRANTED"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "purpose": "PERSONALIZED_RECOMMENDATIONS",
  "status": "GRANTED",
  "policy_version": "demo-1.0",
  "recorded_at": "2026-09-24T16:17:10.723536",
  "withdrawn_at": null
}
```

---

### XXI. Danh sách lý do đánh giá
* **Endpoint:** `GET /api/v1/feedback/reasons`
* **Mô tả:** Các lý do cố định cho 👍/👎.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "up": [
    "accurate",
    "complete",
    "well_presented"
  ],
  "down": [
    "inaccurate",
    "incomplete",
    "off_topic"
  ]
}
```

---

### XXII. Đánh giá câu trả lời
* **Endpoint:** `PUT /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback`
* **Mô tả:** Ghi hoặc sửa đánh giá `up`/`down` kèm một lý do cố định (không có ô chữ tự do). `message_id` lấy từ sự kiện `done`. Lý do không hợp với rating → `400`; tin không thuộc hội thoại → `404`.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`, hội thoại phải thuộc phiên này.
* **URL Params:**
  * `message_id` *(number, required)*: ID câu trả lời, lấy từ `message_id` của sự kiện `done`.
  * `conversation_id` *(string, required)*: ID hội thoại.

#### Request Body
```json
{
  "rating": "down",
  "reason": "off_topic"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "message_id": 130,
  "rating": "down",
  "reason": "off_topic",
  "context_type": "TRAVEL",
  "locale": "vi",
  "created_at": "2026-09-24T16:17:10.669476",
  "updated_at": "2026-09-24T16:17:10.669476"
}
```

---

### XXIII. Xem đánh giá
* **Endpoint:** `GET /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback`
* **Mô tả:** Đánh giá hiện có của một câu trả lời; `null` khi chưa đánh giá.
* **Phân quyền:** Gửi cookie phiên, hội thoại phải thuộc phiên này.
* **URL Params:**
  * `message_id` *(number, required)*: ID câu trả lời, lấy từ `message_id` của sự kiện `done`.
  * `conversation_id` *(string, required)*: ID hội thoại.

#### Response Mẫu (`200 OK`)
```json
{
  "message_id": 130,
  "rating": "down",
  "reason": "off_topic",
  "context_type": "TRAVEL",
  "locale": "vi",
  "created_at": "2026-09-24T16:17:10.669476",
  "updated_at": "2026-09-24T16:17:10.669476"
}
```

---

### XXIV. Rút lại đánh giá
* **Endpoint:** `DELETE /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback`
* **Mô tả:** Xóa đánh giá của một câu trả lời; không có gì để xóa vẫn `204`.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`, hội thoại phải thuộc phiên này.
* **URL Params:**
  * `message_id` *(number, required)*: ID câu trả lời, lấy từ `message_id` của sự kiện `done`.
  * `conversation_id` *(string, required)*: ID hội thoại.

#### Response Mẫu (`204 No Content`)
Không có nội dung.

---

### XXV. Tổng hợp đánh giá
* **Endpoint:** `GET /api/v1/feedback/summary`
* **Mô tả:** Số đánh giá và lý do theo loại (không có nội dung chat).
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **Query Parameters:**
  * `context_type` *(string | null, optional)*: `TRAVEL` hoặc `HOTEL`.

#### Response Mẫu (`200 OK`)
```json
{
  "total": 5,
  "up": 5,
  "down": 0,
  "satisfaction": 1.0,
  "reasons": {
    "up": {
      "accurate": 1
    },
    "down": {}
  },
  "context_type": "TRAVEL"
}
```

---

### XXVI. Ảnh thẻ địa điểm
* **Endpoint:** `GET /api/v1/media/catalogue/{name}`
* **Mô tả:** Ảnh SVG của thẻ địa điểm, chỉ ảnh có trong danh mục đang nạp (`CATALOGUE_PATH`); kèm `nosniff`, CSP sandbox, cache 1 ngày. Tên không khớp mẫu `^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.svg$` hoặc không có → `404`.
* **Phân quyền:** Public.
* **URL Params:** `name` *(string, required)*: Tên tệp ảnh trong danh mục.

#### Response Mẫu (`200 OK`)
Trả về tệp `image/svg+xml`.

---

### XXVII. Mã phòng demo
* **Endpoint:** `GET /api/v1/demo/scenarios`
* **Mô tả:** Mã kết nối và PIN của các phòng demo. Chỉ khi `UNIFIED_DEMO_ENABLED=true` và không phải production; ngược lại `404`.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "enabled": true,
  "scenarios": [
    {
      "room_number": "305",
      "connect_code": "demo-an-nhien-305",
      "pin": "30…",
      "hotel_slug": "an-nhien-son-tra-demo"
    },
    {
      "room_number": "410",
      "connect_code": "demo-an-nhien-410",
      "pin": "41…",
      "hotel_slug": "an-nhien-son-tra-demo"
    },
    {
      "room_number": "208",
      "connect_code": "demo-an-nhien-208",
      "pin": "20…",
      "hotel_slug": "an-nhien-son-tra-demo"
    }
  ]
}
```

---

### XXVIII. Kết nối kỳ lưu trú — bước 1
* **Endpoint:** `POST /api/v1/stays/connect`
* **Mô tả:** Đổi mã phòng (mã QR) lấy `connection_id` để nhập PIN. Mã sai → `404`; phòng đang bị khóa vì nhập sai PIN nhiều lần → `429`.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`.

#### Request Body
```json
{
  "connect_code": "demo-an-nhien-305"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "connection_id": "260146a9-381c-47d8-8898-f0c7420aab56",
  "hotel_slug": "an-nhien-son-tra-demo",
  "hotel_name": "Khách sạn An Nhiên Sơn Trà DEMO",
  "city": "Đà Nẵng",
  "verification_required": true,
  "expires_at": "2026-09-24T16:27:10.748229"
}
```

---

### XXIX. Kết nối kỳ lưu trú — bước 2 (PIN)
* **Endpoint:** `POST /api/v1/stays/verify`
* **Mô tả:** Xác minh PIN, nhận `access_token` dùng làm header `X-Stay-Token` và phạm vi quyền. Sai PIN → `401`; quá số lần → `429` (kể cả PIN đúng) tới khi lễ tân mở khóa; `connection_id` hết hạn → `410`; phòng không có kỳ lưu trú → `404`.
* **Phân quyền:** Gửi cookie phiên và `X-CSRF-Token: <csrf_token>`.

#### Request Body
```json
{
  "connection_id": "260146a9-381c-47d8-8898-f0c7420aab56",
  "pin": "30…"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "access_token": "aTyz1_KWa3u9…",
  "expires_at": "2026-09-25T00:17:10.763718",
  "stay": {
    "id": "demo-stay-305",
    "hotel_slug": "an-nhien-son-tra-demo",
    "hotel_name": "Khách sạn An Nhiên Sơn Trà DEMO",
    "room_number": "305",
    "guest_alias": "Khách demo phòng 305",
    "check_in_at": "2026-09-24T14:16:51.156024Z",
    "check_out_at": "2026-09-26T16:16:51.156024Z",
    "scopes": [
      "hotel:read",
      "service:create",
      "service:read"
    ]
  }
}
```

---

### XXX. Xem kỳ lưu trú hiện tại
* **Endpoint:** `GET /api/v1/stays/active`
* **Mô tả:** Kỳ lưu trú của token gửi kèm. Không gửi token hoặc token hết hạn → `{"active": false, "stay": null}`.
* **Phân quyền:** Gửi `X-Stay-Token: <access_token>` (tùy chọn).

#### Response Mẫu (`200 OK`)
```json
{
  "active": true,
  "stay": {
    "id": "demo-stay-305",
    "hotel_slug": "an-nhien-son-tra-demo",
    "hotel_name": "Khách sạn An Nhiên Sơn Trà DEMO",
    "room_number": "305",
    "guest_alias": "Khách demo phòng 305",
    "check_in_at": "2026-09-24T14:16:51.156024Z",
    "check_out_at": "2026-09-26T16:16:51.156024Z",
    "scopes": [
      "hotel:read",
      "service:create",
      "service:read"
    ]
  }
}
```

---

### XXXI. Ngắt kết nối kỳ lưu trú
* **Endpoint:** `DELETE /api/v1/stays/active`
* **Mô tả:** Thu hồi token lưu trú của thiết bị này. Token thiếu/không hợp lệ → `401`.
* **Phân quyền:** Gửi `X-Stay-Token: <access_token>`.

#### Response Mẫu (`204 No Content`)
Không có nội dung.

---

### XXXII. Danh mục dịch vụ khách sạn
* **Endpoint:** `GET /api/v1/hotel/services`
* **Mô tả:** Dịch vụ của khách sạn đang lưu trú (giá, giờ phục vụ, cần xác nhận hay không). Cần quyền `hotel:read`; thiếu token → `401`, thiếu quyền → `403`.
* **Phân quyền:** Gửi `X-Stay-Token: <access_token>`.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": "demo-svc_clean_now",
    "code": "svc_clean_now",
    "category": "housekeeping",
    "name": "Dọn phòng ngay",
    "description": "Thời gian phục vụ: 08:00-20:00.",
    "price": 0,
    "currency": "VND",
    "hours": "08:00-20:00",
    "estimated_minutes": 30,
    "requires_confirmation": true
  },
  {
    "id": "demo-svc_water",
    "code": "svc_water",
    "category": "housekeeping",
    "name": "Thêm hai chai nước 500 ml",
    "description": "Thời gian phục vụ: 24/7.",
    "price": 0,
    "currency": "VND",
    "hours": "24/7",
    "estimated_minutes": 15,
    "requires_confirmation": true
  },
  {
    "id": "demo-svc_towel",
    "code": "svc_towel",
    "category": "housekeeping",
    "name": "Thêm khăn tắm",
    "description": "Thời gian phục vụ: 24/7.",
    "price": 0,
    "currency": "VND",
    "hours": "24/7",
    "estimated_minutes": 15,
    "requires_confirmation": true
  }
]
```

---

### XXXIII. Địa điểm quanh khách sạn
* **Endpoint:** `GET /api/v1/hotel/nearby`
* **Mô tả:** Địa điểm công khai quanh khách sạn đang lưu trú. Không có token hợp lệ → `[]`.
* **Phân quyền:** Gửi `X-Stay-Token: <access_token>` (tùy chọn).

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": "demo-knowledge-place-poi_my_khe",
    "title": "Biển Mỹ Khê",
    "content": "Biển Mỹ Khê cách khách sạn 1.4 km, ngoài trời. Nên kiểm tra thời tiết và cảnh báo biển trước khi gợi ý."
  },
  {
    "id": "demo-knowledge-place-poi_son_tra",
    "title": "Bán đảo Sơn Trà",
    "content": "Bán đảo Sơn Trà cách khách sạn 7.2 km, ngoài trời. Phù hợp buổi sáng khô ráo. Không gợi ý khi cảnh báo mưa lớn."
  },
  {
    "id": "demo-knowledge-place-poi_cafe_demo",
    "title": "Cafe Gió Biển DEMO",
    "content": "Cafe Gió Biển DEMO cách khách sạn 0.8 km, trong nhà. Địa điểm giả lập, có khu vực trong nhà."
  }
]
```

---

### XXXIV. Gửi yêu cầu dịch vụ
* **Endpoint:** `POST /api/v1/service-requests`
* **Mô tả:** Gửi yêu cầu (khăn tắm, dọn phòng, xe sân bay…) tới khách sạn. Cần quyền `service:create`. Gửi lại cùng `idempotency_key` trả về yêu cầu cũ, không tạo trùng. Dịch vụ cần xác nhận mà `confirmed=false` → `409`; dịch vụ không thuộc khách sạn → `404`.
* **Phân quyền:** Gửi `X-Stay-Token: <access_token>`.

#### Request Body
```json
{
  "service_item_id": "demo-svc_clean_now",
  "quantity": 1,
  "note": "Sau 9 giờ sáng",
  "confirmed": true,
  "idempotency_key": "vi-du-yeu-cau-0001"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "id": "5dba7448-949d-4a55-9508-b753dc9af865",
  "service_item_id": "demo-svc_clean_now",
  "service_name": "Dọn phòng ngay",
  "quantity": 1,
  "note": "Sau 9 giờ sáng",
  "status": "SUBMITTED",
  "total_amount": 0,
  "currency": "VND",
  "created_at": "2026-09-24T16:17:10.870962"
}
```

---

### XXXV. Danh sách yêu cầu dịch vụ
* **Endpoint:** `GET /api/v1/service-requests`
* **Mô tả:** Các yêu cầu dịch vụ của kỳ lưu trú. Cần quyền `service:read`.
* **Phân quyền:** Gửi `X-Stay-Token: <access_token>`.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": "5dba7448-949d-4a55-9508-b753dc9af865",
    "service_item_id": "demo-svc_clean_now",
    "service_name": "Dọn phòng ngay",
    "quantity": 1,
    "note": "Sau 9 giờ sáng",
    "status": "SUBMITTED",
    "total_amount": 0,
    "currency": "VND",
    "created_at": "2026-09-24T16:17:10.870962"
  }
]
```

---

### XXXVI. Lễ tân cấp PIN cho kỳ lưu trú
* **Endpoint:** `POST /api/v1/admin/hotels/{hotel_slug}/rooms/{room_number}/stay-pin`
* **Mô tả:** Cấp PIN 6 số ngẫu nhiên cho kỳ lưu trú đang diễn ra của phòng; PIN phòng cũ không còn mở kỳ này. `revoke_devices=true` thu hồi các thiết bị đã kết nối. PIN chỉ trả về một lần. Không có phòng hoặc kỳ lưu trú đang diễn ra → `404`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **URL Params:**
  * `hotel_slug` *(string, required)*: Slug khách sạn.
  * `room_number` *(string, required)*: Số phòng.
* **Query Parameters:**
  * `revoke_devices` *(boolean, optional, default: false)*: Thu hồi luôn các thiết bị đã kết nối.
* **Request Body:** Không có.

#### Response Mẫu (`200 OK`)
```json
{
  "room_number": "410",
  "stay_id": "demo-stay-410",
  "pin": "40…",
  "valid_until": "2026-09-26T16:16:51.156024",
  "revoked_devices": 0
}
```

---

### XXXVII. Lễ tân mở khóa phòng
* **Endpoint:** `POST /api/v1/admin/hotels/{hotel_slug}/rooms/{room_number}/pin-unlock`
* **Mô tả:** Mở khóa phòng bị khóa vì nhập sai PIN: các lần sai trước thôi tính, thử thách đang mở bị đóng.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **URL Params:**
  * `hotel_slug` *(string, required)*: Slug khách sạn.
  * `room_number` *(string, required)*: Số phòng.
* **Request Body:** Không có.

#### Response Mẫu (`200 OK`)
```json
{
  "room_number": "410",
  "cleared_at": "2026-09-24T16:17:11.000812",
  "closed_challenges": 0
}
```

---

### XXXVIII. Tạo đối tác và khóa API
* **Endpoint:** `POST /api/v1/admin/partners`
* **Mô tả:** Tạo đối tác; khóa `api_key` chỉ hiện một lần, hệ thống chỉ lưu SHA-256. Slug đã có → `409`. Hướng dẫn tích hợp: [PARTNER_API.md](PARTNER_API.md).
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.

#### Request Body
```json
{
  "slug": "tour-abc",
  "name": "Tour ABC",
  "rate_limit_per_minute": 120
}
```

#### Response Mẫu (`201 Created`)
```json
{
  "slug": "tour-abc",
  "name": "Tour ABC",
  "key_prefix": "vsk_6406597b",
  "rate_limit_per_minute": 120,
  "is_active": true,
  "created_at": "2026-09-24T16:17:11.020142",
  "last_used_at": null,
  "api_key": "vsk_6406597b…"
}
```

---

### XXXIX. Danh sách đối tác
* **Endpoint:** `GET /api/v1/admin/partners`
* **Mô tả:** Danh sách đối tác (không bao giờ trả khóa).
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "slug": "tour-abc",
    "name": "Tour ABC",
    "key_prefix": "vsk_6406597b",
    "rate_limit_per_minute": 120,
    "is_active": true,
    "created_at": "2026-09-24T16:17:11.020142",
    "last_used_at": null
  }
]
```

---

### XL. Đổi khóa đối tác
* **Endpoint:** `POST /api/v1/admin/partners/{slug}/rotate-key`
* **Mô tả:** Cấp khóa mới; khóa cũ hết hiệu lực ngay; hội thoại vẫn thuộc đối tác; bật lại đối tác đã bị khóa.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.
* **Request Body:** Không có.

#### Response Mẫu (`200 OK`)
```json
{
  "slug": "tour-abc",
  "name": "Tour ABC",
  "key_prefix": "vsk_b59b638e",
  "rate_limit_per_minute": 120,
  "is_active": true,
  "created_at": "2026-09-24T16:17:11.020142",
  "last_used_at": null,
  "api_key": "vsk_b59b638e…"
}
```

---

### XLI. Khóa đối tác
* **Endpoint:** `POST /api/v1/admin/partners/{slug}/deactivate`
* **Mô tả:** Mọi lượt gọi bằng khóa của đối tác trả `401` cho tới khi đổi khóa.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.
* **Request Body:** Không có.

#### Response Mẫu (`200 OK`)
```json
{
  "slug": "tour-abc",
  "name": "Tour ABC",
  "key_prefix": "vsk_b59b638e",
  "rate_limit_per_minute": 120,
  "is_active": false,
  "created_at": "2026-09-24T16:17:11.020142",
  "last_used_at": "2026-09-24T16:17:11.041943"
}
```

---

### XLII. Đối tác tạo hội thoại
* **Endpoint:** `POST /api/v1/partner/conversations`
* **Mô tả:** Tạo hội thoại Travel cho một người dùng của đối tác.
* **Phân quyền:** Gửi `Authorization: Bearer <khóa đối tác>` và `X-Partner-User: <mã người dùng>`.

#### Request Body
```json
{
  "locale": "vi"
}
```

#### Response Mẫu (`201 Created`)
```json
{
  "id": "ac59778c-f557-4f71-b0af-4bf3683e90c4",
  "locale": "vi",
  "destination_slug": null,
  "created_at": "2026-09-24T16:17:11.047376"
}
```

---

### XLIII. Đối tác gửi tin (JSON một lần)
* **Endpoint:** `POST /api/v1/partner/conversations/{conversation_id}/messages`
* **Mô tả:** Gửi tin và nhận cả câu trả lời một lần; cùng lõi xử lý với PWA, chỉ chế độ Travel. Lỗi giữa chừng → `502`; đủ số lượt chat cùng lúc → `503` + `Retry-After`; vượt giới hạn của đối tác → `429`.
* **Phân quyền:** Gửi `Authorization: Bearer <khóa đối tác>` và `X-Partner-User: <mã người dùng>`.
* **URL Params:** `conversation_id` *(string, required)*: ID hội thoại.

#### Request Body
```json
{
  "message": "Lên lịch trình 2 ngày 1 đêm ở Hồ Thác Bà"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "conversation_id": "ac59778c-f557-4f71-b0af-4bf3683e90c4",
  "message_id": 134,
  "reply": "Lịch Trình 2 Ngày 1 Đêm Hồ Thác Bà",
  "suggestions": [
    {
      "id": "context-budget",
      "label": "Thêm ngân sách",
      "prompt": "Gợi ý cho mình lựa chọn ở Hồ Thác Bà trong 2 ngày theo ngân sách phù hợp."
    }
  ],
  "places": [],
  "action": {
    "type": "GENERATIVE_UI",
    "artifact": {
      "id": "artifact_54784a575cb546d3",
      "version": 1,
      "context": "travel_itinerary",
      "responseMode": "artifact",
      "title": "Lịch Trình 2 Ngày 1 Đêm Hồ Thác Bà",
      "days": [
        {
          "id": "day_1",
          "label": "Ngày 1",
          "items": [
            {
              "id": "day_1_item_1",
              "time": "06:00",
              "title": "Hướng dẫn viên đón tại điểm hẹn và khởi hành đi tham quan Hồ Thác Bà",
              "place": "Hà Nội"
            },
            {
              "id": "day_1_item_2",
              "time": "09:20",
              "title": "Đến Bến cảng Hương Lý, di chuyển xuống tàu tham quan Hồ Thác Bà",
              "place": "Bến cảng Hương Lý"
            },
            {
              "id": "day_1_item_3",
              "time": "09:00",
              "title": "Tham quan Nhà máy Thủy điện Thác Bà và dâng hương tại đền thờ chiến sĩ",
              "place": "Nhà máy Thủy điện Thác Bà"
            }
          ]
        },
        {
          "id": "day_2",
          "label": "Ngày 2",
          "items": [
            {
              "id": "day_2_item_1",
              "time": "06:00",
              "title": "Thức dậy sớm, đón bình minh trên Hồ Thác Bà và tản bộ tự do",
              "place": "Hồ Thác Bà"
            },
            {
              "id": "day_2_item_2",
              "time": "07:30",
              "title": "Dùng bữa sáng tại nhà hàng trong khu nghỉ dưỡng",
              "place": "Khu nghỉ dưỡng Hồ Thác Bà"
            },
            {
              "id": "day_2_item_3",
              "time": "16:00",
              "title": "Tàu đưa quý khách về Đảo Hoa để nhận phòng và tự do tham quan",
              "place": "Đảo Hoa"
            }
          ]
        }
      ],
      "layout": "timeline_map"
    },
    "layout": "timeline_map"
  },
  "artifact_id": "artifact_54784a575cb546d3",
  "artifact_version": 1
}
```

---

### XLIV. Đối tác gửi tin (SSE)
* **Endpoint:** `POST /api/v1/partner/conversations/{conversation_id}/messages/stream`
* **Mô tả:** Gửi tin và nhận câu trả lời chảy dần, cùng định dạng sự kiện với PWA (mục 1.2).
* **Phân quyền:** Gửi `Authorization: Bearer <khóa đối tác>` và `X-Partner-User: <mã người dùng>`.
* **URL Params:** `conversation_id` *(string, required)*: ID hội thoại.

#### Request Body
```json
{
  "message": "Hồ Thác Bà có gì chơi?"
}
```

#### Response Mẫu (`200 OK`)
`Content-Type: text/event-stream` — mỗi phần tử dưới đây là một sự kiện SSE (`event` + `data`). Có 136 sự kiện `token`, hiển thị 3 sự kiện đầu; ghép lại được: “Hồ Thác Bà là một điểm đến tuyệt vời với nhiều hoạt động thú vị. Bạn sẽ được tham quan Hồ Thác Bà, Thủy điện Thác Bà và thưởng thức cảnh quan tự nhiên độc đáo tại đây. Ngoài ra, bạn cũng có thể tham gia các trò chơi ngoài trời như câu cá, dã ngoại hoặc thậm chí là đi thuyền trên hồ. Hãy nhớ mang theo đồ ăn và nước uống”.
```json
[
  {
    "event": "token",
    "data": {
      "text": "H"
    }
  },
  {
    "event": "token",
    "data": {
      "text": "ồ"
    }
  },
  {
    "event": "token",
    "data": {
      "text": " Th"
    }
  },
  {
    "event": "done",
    "data": {
      "conversation_id": "ac59778c-f557-4f71-b0af-4bf3683e90c4",
      "resolved_context": "TRAVEL",
      "suggestions": [
        {
          "id": "context-budget",
          "label": "Thêm ngân sách",
          "prompt": "Gợi ý cho mình lựa chọn ở Hồ Thác Bà trong 2 ngày theo ngân sách phù hợp."
        }
      ],
      "message_id": 136
    }
  }
]
```

---

### XLV. Đối tác xem lịch sử
* **Endpoint:** `GET /api/v1/partner/conversations/{conversation_id}/messages`
* **Mô tả:** 200 tin gần nhất, cũ → mới.
* **Phân quyền:** Gửi `Authorization: Bearer <khóa đối tác>` và `X-Partner-User: <mã người dùng>`.
* **URL Params:** `conversation_id` *(string, required)*: ID hội thoại.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": 133,
    "role": "user",
    "content": "Lên lịch trình 2 ngày 1 đêm ở Hồ Thác Bà",
    "created_at": "2026-09-24T16:17:21.300815"
  },
  {
    "id": 134,
    "role": "assistant",
    "content": "Lịch Trình 2 Ngày 1 Đêm Hồ Thác Bà",
    "created_at": "2026-09-24T16:17:21.300815"
  },
  {
    "id": 135,
    "role": "user",
    "content": "Hồ Thác Bà có gì chơi?",
    "created_at": "2026-09-24T16:17:24.309484"
  }
]
```

---

### XLVI. Đối tác xóa hội thoại
* **Endpoint:** `DELETE /api/v1/partner/conversations/{conversation_id}`
* **Mô tả:** Xóa hội thoại cùng tin nhắn, đánh giá và consent (khi khách yêu cầu xóa dữ liệu).
* **Phân quyền:** Gửi `Authorization: Bearer <khóa đối tác>` và `X-Partner-User: <mã người dùng>`.
* **URL Params:** `conversation_id` *(string, required)*: ID hội thoại.

#### Response Mẫu (`204 No Content`)
Không có nội dung.

---

### XLVII. Chat Travel (JSON)
* **Endpoint:** `POST /api/chat`
* **Mô tả:** Trả lời một lần. Mỗi câu trả lời có `conversation_id` (bằng `session_id`); gửi lại `conversation_id` (hoặc `session_id`) ở lượt sau để bot nhớ các lượt trước. Bot nhớ 4 lượt hỏi-đáp gần nhất; hội thoại hết hạn sau 2 giờ không dùng. Không gửi id, id lạ hoặc đã hết hạn → bắt đầu hội thoại mới với id mới (luôn dùng id trong câu trả lời mới nhất). Gửi cả hai tên với hai giá trị khác nhau → `422`.
* **Phân quyền:** Public.

#### Request Body
```json
{
  "message": "Hồ Thác Bà có gì chơi?"
}
```

#### Response Mẫu — lượt 1 (`200 OK`)
```json
{
  "reply": "Hồ Thác Bà ở Yên Bái có nhiều điều để tham quan và trải nghiệm. Bạn sẽ được thưởng thức khung cảnh đẹp như chốn bồng lai tiên cảnh, với hơn 1.300 hòn đảo lớn nhỏ nổi lên trên sóng nước. Ngoài ra, bạn còn có thể tham gia các hoạt động như thăm mô hình chăn nuôi thủy sản, và trải nghiệm hái dưa hấu tại thời điểm thích hợ…",
  "session_id": "1113eeb0-f575-4adf-8883-ef1b36ba7fed",
  "country_code": "VN",
  "destination_slug": null,
  "sources": [],
  "conversation_id": "1113eeb0-f575-4adf-8883-ef1b36ba7fed"
}
```

#### Request Body — lượt 2, gửi lại conversation_id
```json
{
  "message": "Đi thuyền ở đó mất bao lâu?",
  "conversation_id": "1113eeb0-f575-4adf-8883-ef1b36ba7fed"
}
```

#### Response Mẫu — lượt 2, gửi lại conversation_id (`200 OK`)
```json
{
  "reply": "Thông tin trong dữ liệu hiện tại không cung cấp thời gian cụ thể để đi thuyền trên Hồ Thác Bà. Để có thông tin chính xác, bạn nên liên hệ trực tiếp với công ty du lịch hoặc trang web của họ. Họ sẽ cung cấp cho bạn thông tin chi tiết về thời gian di chuyển và các hoạt động trên hồ.",
  "session_id": "1113eeb0-f575-4adf-8883-ef1b36ba7fed",
  "country_code": "VN",
  "destination_slug": null,
  "sources": [],
  "conversation_id": "1113eeb0-f575-4adf-8883-ef1b36ba7fed"
}
```

---

### XLVIII. Chat Travel (SSE)
* **Endpoint:** `POST /api/chat/stream`
* **Mô tả:** Trả lời chảy dần: `token`, `replace`, `done` (`conversation_id`, `session_id`), `error`. Giữ hội thoại giống `POST /api/chat`. Đủ số lượt chat cùng lúc → `503` + `Retry-After`.
* **Phân quyền:** Public.

#### Request Body
```json
{
  "message": "Còn ăn gì ở đó?",
  "conversation_id": "1113eeb0-f575-4adf-8883-ef1b36ba7fed"
}
```

#### Response Mẫu (`200 OK`)
`Content-Type: text/event-stream` — mỗi phần tử dưới đây là một sự kiện SSE (`event` + `data`). Có 87 sự kiện `token`, hiển thị 3 sự kiện đầu; ghép lại được: “Trong chương trình tour, khách hàng có cơ hội thưởng thức ẩm thực đậm chất Hồ Thác Bà tại khu vực tham quan. Tuy nhiên, thông tin cụ thể về các món ăn được phục vụ trong bữa trưa không được cung cấp trong dữ liệu hiện tại. Bạn nên liên hệ với công ty du lịch để biết thêm chi tiết về thực đơn và các lựa chọn ẩm thực tại”.
```json
[
  {
    "event": "token",
    "data": {
      "text": "Tr"
    }
  },
  {
    "event": "token",
    "data": {
      "text": "ong"
    }
  },
  {
    "event": "token",
    "data": {
      "text": " chương"
    }
  },
  {
    "event": "done",
    "data": {
      "session_id": "1113eeb0-f575-4adf-8883-ef1b36ba7fed",
      "conversation_id": "1113eeb0-f575-4adf-8883-ef1b36ba7fed"
    }
  }
]
```

---

### XLIX. Xem kết quả tìm tài liệu
* **Endpoint:** `POST /api/rag-test`
* **Mô tả:** Các đoạn tài liệu bước tìm kiếm trả về cho một câu hỏi; không gọi model chat. `destination_slug` không có → `404`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.

#### Request Body
```json
{
  "question": "Hồ Thác Bà có gì chơi?",
  "top_k": 2
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "question": "Hồ Thác Bà có gì chơi?",
  "country_code": "VN",
  "destination_slug": null,
  "results": [
    {
      "id": "1d9429ac-5f21-4e04-b8db-277079d819c8-0",
      "text": "Tên chương trình: HVNT 1N HỒ THÁC BÀ\nĐiểm đến liên quan: Hồ Thác Bà, Thủy điện Thác Bà, Yên Bái, Hà Nội, Đảo Hoa, Đảo Xanh, Đảo Thiên Đường\nPhần: Chương Trình Du Lịch\n\nHVTB YÊN BÁI - KHÁM PHÁ HỒ THÁC BÀ - THUỶ ĐIỆN -\nĐẢO THIÊN ĐƯỜNG - ĐẢO XANH\nThời gian: 01 Ngày | Loại hình: Khách Đoàn\nKhởi hành: Theo yêu cầu | Phương …",
      "metadata": {
        "document_id": "6e3cf4e3-0440-4393-9142-9cccf57c316b",
        "document_version_id": "1d9429ac-5f21-4e04-b8db-277079d819c8",
        "source": "HVNT 1N HỒ THÁC BÀ.pdf",
        "filename": "HVNT 1N HỒ THÁC BÀ.pdf",
        "section": "Chương Trình Du Lịch",
        "chunk_index": 0,
        "country_code": "VN",
        "primary_destination_slug": "ho-thac-ba",
        "primary_destination_name": "Hồ Thác Bà",
        "locations": "Hồ Thác Bà | Thủy điện Thác Bà | Yên Bái | Hà Nội | Đảo Hoa | Đảo Xanh | Đảo Thiên Đường",
        "embedding_model": "qwen3-embedding:0.6b",
        "chunker_version": "travel-tour-v2-policy",
        "provenance": "real",
        "synthetic": false,
        "authority": "source",
        "dataset_id": null
      },
      "distance": 0.4468182607522567,
      "keyword_score": 1.0,
      "vector_rank": 13,
      "source": "HVNT 1N HỒ THÁC BÀ.pdf"
    },
    {
      "id": "cafc8364-2ace-40bf-9d17-f37837d1142f-0",
      "text": "Tên chương trình: HVNT 2N1Đ HỒ THÁC BÀ\nĐiểm đến liên quan: Hồ Thác Bà, Thủy điện Thác Bà, Yên Bái, Hà Nội, Đảo Hoa, Đảo Xanh, Đảo Thiên Đường\nPhần: Chương Trình Du Lịch\n\nHVTB 0006-23 YÊN BÁI - HỒ THÁC BÀ - ĐẢO HOA - THUỶ ĐIỆN\nThời gian: 02 Ngày 01 Đêm | Loại hình: Khách Đoàn\nKhởi hành: Theo yêu cầu | Phương tiện: Tàu d…",
      "metadata": {
        "document_id": "2c6efe59-4eb5-407c-b8c0-db317412a5bd",
        "document_version_id": "cafc8364-2ace-40bf-9d17-f37837d1142f",
        "source": "HVNT 2N1Đ HỒ THÁC BÀ.pdf",
        "filename": "HVNT 2N1Đ HỒ THÁC BÀ.pdf",
        "section": "Chương Trình Du Lịch",
        "chunk_index": 0,
        "country_code": "VN",
        "primary_destination_slug": "ho-thac-ba",
        "primary_destination_name": "Hồ Thác Bà",
        "locations": "Hồ Thác Bà | Thủy điện Thác Bà | Yên Bái | Hà Nội | Đảo Hoa | Đảo Xanh | Đảo Thiên Đường",
        "embedding_model": "qwen3-embedding:0.6b",
        "chunker_version": "travel-tour-v2-policy",
        "provenance": "real",
        "synthetic": false,
        "authority": "source",
        "dataset_id": null
      },
      "distance": 0.44944380652437177,
      "keyword_score": 1.0,
      "vector_rank": 22,
      "source": "HVNT 2N1Đ HỒ THÁC BÀ.pdf"
    }
  ]
}
```

---

### L. Tạo điểm đến
* **Endpoint:** `POST /api/destinations`
* **Mô tả:** Tạo một điểm đến để gắn tài liệu. Slug đã có → `409`; `parent_id` không tồn tại → `400`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.

#### Request Body
```json
{
  "slug": "vi-du-da-lat",
  "name": "Đà Lạt",
  "destination_type": "city",
  "province_name": "Lâm Đồng",
  "aliases": "Da Lat,Dalat"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "slug": "vi-du-da-lat",
  "name": "Đà Lạt",
  "destination_type": "city",
  "country_code": "VN",
  "country_name": "Việt Nam",
  "province_name": "Lâm Đồng",
  "parent_id": null,
  "aliases": "Da Lat,Dalat",
  "latitude": null,
  "longitude": null,
  "id": "058e304f-3980-4e24-93e4-0d85a3cc13fd",
  "created_at": "2026-09-24T16:16:54.310854"
}
```

---

### LI. Danh sách điểm đến
* **Endpoint:** `GET /api/destinations`
* **Mô tả:** Danh sách điểm đến, lọc theo quốc gia.
* **Phân quyền:** Public.
* **Query Parameters:**
  * `country_code` *(string, optional, default: "VN")*: Mã quốc gia ISO 2 chữ.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "slug": "ho-thac-ba",
    "name": "Hồ Thác Bà",
    "destination_type": "lake",
    "country_code": "VN",
    "country_name": "Việt Nam",
    "province_name": "Yên Bái",
    "parent_id": "1d291a95-cc43-4689-b6fd-699410090c6a",
    "aliases": "Ho Thac Ba | Thac Ba Lake",
    "latitude": null,
    "longitude": null,
    "id": "e1e37634-65c4-4b1e-8ea5-eaafbdebc357",
    "created_at": "2026-09-23T10:09:07.526840"
  },
  {
    "slug": "viet-nam",
    "name": "Việt Nam",
    "destination_type": "country",
    "country_code": "VN",
    "country_name": "Việt Nam",
    "province_name": null,
    "parent_id": null,
    "aliases": "VN | Vietnam | Việt Nam",
    "latitude": null,
    "longitude": null,
    "id": "924c03ee-104f-4462-8635-c0cf1833ccb0",
    "created_at": "2026-09-23T10:09:07.520352"
  },
  {
    "slug": "yen-bai",
    "name": "Yên Bái",
    "destination_type": "region",
    "country_code": "VN",
    "country_name": "Việt Nam",
    "province_name": "Yên Bái",
    "parent_id": "924c03ee-104f-4462-8635-c0cf1833ccb0",
    "aliases": "Yen Bai",
    "latitude": null,
    "longitude": null,
    "id": "1d291a95-cc43-4689-b6fd-699410090c6a",
    "created_at": "2026-09-23T10:09:07.525553"
  }
]
```

---

### LII. Xem điểm đến
* **Endpoint:** `GET /api/destinations/{slug}`
* **Mô tả:** Chi tiết một điểm đến. Không có → `404`.
* **Phân quyền:** Public.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.

#### Response Mẫu (`200 OK`)
```json
{
  "slug": "vi-du-da-lat",
  "name": "Đà Lạt",
  "destination_type": "city",
  "country_code": "VN",
  "country_name": "Việt Nam",
  "province_name": "Lâm Đồng",
  "parent_id": null,
  "aliases": "Da Lat,Dalat",
  "latitude": null,
  "longitude": null,
  "id": "058e304f-3980-4e24-93e4-0d85a3cc13fd",
  "created_at": "2026-09-24T16:16:54.310854"
}
```

---

### LIII. Tải PDF lên kho tri thức
* **Endpoint:** `POST /api/destinations/{slug}/documents`
* **Mô tả:** Tải một PDF cho điểm đến và xếp hàng nạp (tách đoạn, tạo embedding) chạy nền. `source_key` tùy chọn (mặc định `upload:<slug>:<tên tệp>`); tải lại cùng nguồn tạo phiên bản mới, nội dung trùng thì `changed=false`. Không phải PDF → `400`; không có điểm đến → `404`; PDF không đọc được → `422`. Theo dõi bằng `GET /api/ingestion-jobs/{job_id}`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.
* **Query Parameters:**
  * `source_key` *(string | null, optional)*: Định danh nguồn của tài liệu.
* **Request Body:** `multipart/form-data`, trường `file` là tệp PDF.

#### Response Mẫu (`202 Accepted`)
```json
{
  "changed": true,
  "source": {
    "id": "5a6031cc-1851-4f02-8e51-042a29ec5175",
    "source_key": "upload:vi-du-da-lat:gioi-thieu-da-lat.pdf",
    "primary_destination_id": "058e304f-3980-4e24-93e4-0d85a3cc13fd",
    "filename": "gioi-thieu-da-lat.pdf",
    "status": "active",
    "deleted_at": null,
    "created_at": "2026-09-24T16:16:54.337651",
    "updated_at": "2026-09-24T16:16:54.347783"
  },
  "version": {
    "id": "12097edc-de93-4328-87e2-e857c9e12c83",
    "source_document_id": "5a6031cc-1851-4f02-8e51-042a29ec5175",
    "version": 1,
    "content_hash": "a3245b00fa961b5c39029f22f9e6bcdde778254bafb643dab01c9fdc7f26f6e4",
    "status": "pending",
    "is_active": false,
    "error_message": null,
    "chunk_count": 0,
    "embedding_model": "qwen3-embedding:0.6b",
    "chunker_version": "travel-tour-v2-policy",
    "created_at": "2026-09-24T16:16:54.347783",
    "processed_at": null
  },
  "job": {
    "id": "d975b151-ffda-406e-8324-afaef9e60c50",
    "source_document_id": "5a6031cc-1851-4f02-8e51-042a29ec5175",
    "document_version_id": "12097edc-de93-4328-87e2-e857c9e12c83",
    "status": "pending",
    "attempts": 0,
    "error_message": null,
    "created_at": "2026-09-24T16:16:54.349293",
    "started_at": null,
    "completed_at": null
  }
}
```

---

### LIV. Danh sách tài liệu của điểm đến
* **Endpoint:** `GET /api/destinations/{slug}/documents`
* **Mô tả:** Tài liệu nguồn của điểm đến, mới cập nhật trước. `include_deleted=true` để thấy cả tài liệu đã xóa.
* **Phân quyền:** Public.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.
* **Query Parameters:**
  * `include_deleted` *(boolean, optional, default: false)*: Có lấy cả tài liệu đã xóa không.
  * `offset` *(number, optional, default: 0)*: Bỏ qua bao nhiêu bản ghi đầu.
  * `limit` *(number, optional, default: 50)*: Số bản ghi tối đa.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": "5a6031cc-1851-4f02-8e51-042a29ec5175",
    "source_key": "upload:vi-du-da-lat:gioi-thieu-da-lat.pdf",
    "primary_destination_id": "058e304f-3980-4e24-93e4-0d85a3cc13fd",
    "filename": "gioi-thieu-da-lat.pdf",
    "status": "active",
    "deleted_at": null,
    "created_at": "2026-09-24T16:16:54.337651",
    "updated_at": "2026-09-24T16:16:56.360759"
  }
]
```

---

### LV. Các phiên bản của tài liệu
* **Endpoint:** `GET /api/documents/{document_id}/versions`
* **Mô tả:** Các phiên bản của một tài liệu, mới nhất trước. Không có tài liệu → `404`.
* **Phân quyền:** Public.
* **URL Params:** `document_id` *(string, required)*: ID tài liệu nguồn.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": "12097edc-de93-4328-87e2-e857c9e12c83",
    "source_document_id": "5a6031cc-1851-4f02-8e51-042a29ec5175",
    "version": 1,
    "content_hash": "a3245b00fa961b5c39029f22f9e6bcdde778254bafb643dab01c9fdc7f26f6e4",
    "status": "done",
    "is_active": true,
    "error_message": null,
    "chunk_count": 5,
    "embedding_model": "qwen3-embedding:0.6b",
    "chunker_version": "travel-tour-v2-policy",
    "created_at": "2026-09-24T16:16:54.347783",
    "processed_at": "2026-09-24T16:16:56.360759"
  }
]
```

---

### LVI. Xóa tài liệu
* **Endpoint:** `DELETE /api/documents/{document_id}`
* **Mô tả:** Xóa mềm: đánh dấu `deleted` và tắt mọi phiên bản, chatbot không dùng nữa. Không có tài liệu → `404`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **URL Params:** `document_id` *(string, required)*: ID tài liệu nguồn.

#### Response Mẫu (`204 No Content`)
Không có nội dung.

---

### LVII. Danh sách việc nạp tài liệu
* **Endpoint:** `GET /api/ingestion-jobs`
* **Mô tả:** Việc nạp mới trước; lọc theo `status` (`pending`, `processing`, `done`, `failed`).
* **Phân quyền:** Public.
* **Query Parameters:**
  * `status` *(string | null, optional)*: Lọc theo trạng thái.
  * `offset` *(number, optional, default: 0)*: Bỏ qua bao nhiêu bản ghi đầu.
  * `limit` *(number, optional, default: 50)*: Số bản ghi tối đa.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": "d975b151-ffda-406e-8324-afaef9e60c50",
    "source_document_id": "5a6031cc-1851-4f02-8e51-042a29ec5175",
    "document_version_id": "12097edc-de93-4328-87e2-e857c9e12c83",
    "status": "done",
    "attempts": 1,
    "error_message": null,
    "created_at": "2026-09-24T16:16:54.349293",
    "started_at": "2026-09-24T16:16:54.356285",
    "completed_at": "2026-09-24T16:16:56.360759"
  },
  {
    "id": "461b1f3e-b940-4530-935b-d88f0c740a18",
    "source_document_id": "34c40a43-fc5c-4034-8cd9-79d51fee94e0",
    "document_version_id": "3ba84674-ce6e-435b-9e11-3b643c7d9a8f",
    "status": "done",
    "attempts": 1,
    "error_message": null,
    "created_at": "2026-09-23T10:09:08.067082",
    "started_at": "2026-09-23T10:09:42.526928",
    "completed_at": "2026-09-23T10:09:43.729603"
  }
]
```

---

### LVIII. Xem một việc nạp tài liệu
* **Endpoint:** `GET /api/ingestion-jobs/{job_id}`
* **Mô tả:** Trạng thái, số lần thử, lỗi (nếu có) của một việc nạp. Không có → `404`.
* **Phân quyền:** Public.
* **URL Params:** `job_id` *(string, required)*: ID việc nạp tài liệu.

#### Response Mẫu (`200 OK`)
```json
{
  "id": "d975b151-ffda-406e-8324-afaef9e60c50",
  "source_document_id": "5a6031cc-1851-4f02-8e51-042a29ec5175",
  "document_version_id": "12097edc-de93-4328-87e2-e857c9e12c83",
  "status": "done",
  "attempts": 1,
  "error_message": null,
  "created_at": "2026-09-24T16:16:54.349293",
  "started_at": "2026-09-24T16:16:54.356285",
  "completed_at": "2026-09-24T16:16:56.360759"
}
```

---

### LIX. Kiểm tra tiến trình còn sống
* **Endpoint:** `GET /api/health/live`
* **Mô tả:** Không kiểm tra cơ sở dữ liệu hay model; dùng cho bộ cân bằng tải.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "status": "ok",
  "app": "Chatbot VietSage"
}
```

---

### LX. Kiểm tra cơ sở dữ liệu
* **Endpoint:** `GET /api/health/database`
* **Mô tả:** Chạy `SELECT 1` trên cơ sở dữ liệu. Không kết nối được → `503`.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "database_running": true,
  "backend": "sqlite",
  "pgvector_enabled": false
}
```

---

### LXI. Kiểm tra Ollama
* **Endpoint:** `GET /api/health/ollama`
* **Mô tả:** Ollama có chạy không, đã có model chat và model embedding chưa. Không kết nối được Ollama → `503`.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "ollama_running": true,
  "available_models": [
    "qwen2.5:3b",
    "qwen3:4b",
    "qwen3-embedding:0.6b"
  ],
  "chat_model_ready": true,
  "embed_model_ready": true,
  "keep_alive": "24h"
}
```

---

### LXII. Kiểm tra nhà cung cấp model
* **Endpoint:** `GET /api/health/provider`
* **Mô tả:** Nhà cung cấp đang dùng (luôn `ollama`) và tình trạng của nó. Ollama tắt vẫn trả `200`, với `"ollama": {"ollama_running": false, "error": "..."}`.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "active_provider": "ollama",
  "ollama": {
    "ollama_running": true,
    "available_models": [
      "qwen2.5:3b",
      "qwen3:4b",
      "qwen3-embedding:0.6b"
    ],
    "chat_model_ready": true,
    "embed_model_ready": true,
    "keep_alive": "24h"
  }
}
```

---

### LXIII. Chạy thử một lượt chat
* **Endpoint:** `POST /api/health/chat-test`
* **Mô tả:** Gọi thật model chat một lần, không RAG, không lịch sử. Model lỗi → `503`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.

#### Request Body
```json
{
  "message": "Xin chào"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "reply": "Xin chào! Rất vui được giúp bạn. Bạn cần thông tin về điểm đến nào ạ?",
  "provider": "ollama"
}
```

---

### LXIV. Chạy thử tạo embedding
* **Endpoint:** `POST /api/health/embed-test`
* **Mô tả:** Gọi thật model embedding; trả số chiều và 5 giá trị đầu. Model lỗi → `503`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.

#### Request Body
```json
{
  "text": "Hồ Thác Bà"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "dimension": 1024,
  "sample": [
    -0.014209226,
    -0.025057655,
    -0.013792996
  ]
}
```

---

### LXV. Số liệu vận hành
* **Endpoint:** `GET /api/v1/operations/summary`
* **Mô tả:** Thời gian phản hồi, trạng thái và đánh giá tổng hợp; không có nội dung chat.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.

#### Response Mẫu (`200 OK`)
```json
{
  "telemetry": {
    "enabled": false,
    "retention_days": 30,
    "records": 0,
    "statuses": {},
    "routes": {},
    "average_ms": {
      "prepare": null,
      "first_content": null,
      "total": null
    },
    "average_response_chars": null,
    "average_source_count": null,
    "last_event_utc": null
  },
  "feedback": {
    "all": {
      "total": 5,
      "up": 5,
      "down": 0,
      "satisfaction": 1.0,
      "reasons": {
        "up": {
          "accurate": 1
        },
        "down": {}
      },
      "context_type": null
    },
    "travel": {
      "total": 5,
      "up": 5,
      "down": 0,
      "satisfaction": 1.0,
      "reasons": {
        "up": {
          "accurate": 1
        },
        "down": {}
      },
      "context_type": "TRAVEL"
    },
    "hotel": {
      "total": 0,
      "up": 0,
      "down": 0,
      "satisfaction": null,
      "reasons": {
        "up": {},
        "down": {}
      },
      "context_type": "HOTEL"
    }
  }
}
```

---

### LXVI. Tính năng gợi ý chủ động có bật không
* **Endpoint:** `GET /api/v1/proactive/availability`
* **Mô tả:** Trạng thái tính năng nghiên cứu gợi ý chủ động. Mặc định `PROACTIVE_RESEARCH_ENABLED=false`.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
{
  "available": true,
  "ui_toggle": true,
  "fixture_available": true,
  "default_mode": "OFF"
}
```

---

### LXVII. Tạo phiên nghiên cứu
* **Endpoint:** `POST /api/v1/proactive/sessions`
* **Mô tả:** Tạo phiên 8 giờ, trả `token` dùng cho header `X-Research-Token`. Tính năng tắt → `404`.
* **Phân quyền:** Public.
* **Request Body:** Không có.

#### Response Mẫu (`200 OK`)
```json
{
  "token": "M7Xh9Oc5wjct…",
  "mode": "OFF"
}
```

---

### LXVIII. Xem chế độ phiên
* **Endpoint:** `GET /api/v1/proactive/mode`
* **Mô tả:** Chế độ hiện tại: `OFF`, `SHADOW` hoặc `LIVE`.
* **Phân quyền:** Gửi `X-Research-Token: <token>`.

#### Response Mẫu (`200 OK`)
```json
{
  "mode": "LIVE",
  "available": true,
  "fixture": true
}
```

---

### LXIX. Đổi chế độ phiên
* **Endpoint:** `PUT /api/v1/proactive/mode`
* **Mô tả:** Bật `SHADOW`/`LIVE` cần `consent=true`. Thiếu consent hoặc tính năng tắt → `409`; `SHADOW` chưa bật → `403`; phiên đổi đồng thời → `409` (thử lại).
* **Phân quyền:** Gửi `X-Research-Token: <token>`.

#### Request Body
```json
{
  "mode": "LIVE",
  "consent": true
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "mode": "LIVE"
}
```

---

### LXX. Hỏi có nên gợi ý không
* **Endpoint:** `POST /api/v1/proactive/poll`
* **Mô tả:** Mô hình quyết định có gửi một gợi ý (`OFFER`) hay câu hỏi (`ASK`) lúc này không; `event` là `null` khi không gửi, `status` nêu lý do.
* **Phân quyền:** Gửi `X-Research-Token: <token>`.

#### Request Body
```json
{
  "attention_available": true
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "event": {
    "id": "03f9478a-af2a-4fbd-86e9-0bbed3540043",
    "kind": "ASK",
    "context_type": "PROACTIVE",
    "text": "Tình huống giả lập: bạn có muốn tìm chỗ nghỉ sau khi đi bộ không?",
    "transaction_id": "e1e0ade9-f51d-4f78-a7dd-fac92d7ec37f",
    "expires_at": 29837782.406450298
  },
  "status": "POSITIVE_VALUE"
}
```

---

### LXXI. Báo đã hiển thị gợi ý
* **Endpoint:** `POST /api/v1/proactive/receipt`
* **Mô tả:** Xác nhận gợi ý đã hiện cho khách. `status`: `RECEIVED`, `ALREADY_RECEIVED` hoặc `SUPPRESSED`.
* **Phân quyền:** Gửi `X-Research-Token: <token>`.

#### Request Body
```json
{
  "event_id": "03f9478a-af2a-4fbd-86e9-0bbed3540043",
  "attention_available": true
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "status": "RECEIVED"
}
```

---

### LXXII. Gửi câu trả lời của khách
* **Endpoint:** `POST /api/v1/proactive/reply`
* **Mô tả:** Câu trả lời của khách cho câu hỏi chủ động (`yes`, `no`, `uncertain`, `abort`); có thể dẫn tới một gợi ý tiếp theo. Không có câu hỏi đang chờ → `status = NO_PENDING_QUESTION`.
* **Phân quyền:** Gửi `X-Research-Token: <token>`.

#### Request Body
```json
{
  "transaction_id": "e1e0ade9-f51d-4f78-a7dd-fac92d7ec37f",
  "reply_id": "tra-loi-0001",
  "answer": "yes",
  "attention_available": true
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "event": {
    "id": "7d728089-808d-493f-a302-50c33f407909",
    "kind": "OFFER",
    "context_type": "PROACTIVE",
    "text": "Tình huống giả lập: bạn có thể nghỉ tại khu ghế yên tĩnh trong 10 phút. Đây là dữ liệu kiểm thử, không phải dịch vụ thực tế.",
    "transaction_id": "e1e0ade9-f51d-4f78-a7dd-fac92d7ec37f",
    "expires_at": 29837778.41317629
  },
  "status": "LINKED_OFFER"
}
```

---

### LXXIII. Nạp tình huống giả lập
* **Endpoint:** `POST /api/v1/proactive/fixture`
* **Mô tả:** Nạp tình huống D0 để thử (chỉ development, `PROACTIVE_FIXTURE_ENABLED=true`).
* **Phân quyền:** Gửi `X-Research-Token: <token>`.

#### Request Body
```json
{
  "scenario": "question"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "status": "D0-MATH_LOADED"
}
```

---

### LXXIV. Giao diện chat Hotel cũ
* **Endpoint:** `GET /hotel-chat`
* **Mô tả:** Giao diện chat Hotel cũ.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
Trả về tệp `text/html`.

---

### LXXV. Tạo khách sạn (API cũ)
* **Endpoint:** `POST /api/hotels`
* **Mô tả:** Tạo khách sạn trong API Hotel cũ. Slug đã có → `400`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.

#### Request Body
```json
{
  "slug": "khach-san-vi-du",
  "name": "Khách sạn Ví Dụ",
  "city": "Yên Bái"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "id": "5f998c96-1bfb-4b70-8a12-cd0e9033e565",
  "slug": "khach-san-vi-du",
  "name": "Khách sạn Ví Dụ",
  "city": "Yên Bái",
  "checkin_time": null,
  "checkout_time": null,
  "default_language": "vi",
  "supported_languages": "vi,en",
  "latitude": null,
  "longitude": null,
  "created_at": "2026-09-24T16:17:24.807945"
}
```

---

### LXXVI. Danh sách khách sạn (API cũ)
* **Endpoint:** `GET /api/hotels`
* **Mô tả:** Danh sách khách sạn.
* **Phân quyền:** Public.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": "hotel_demo_an_nhien",
    "slug": "an-nhien-son-tra-demo",
    "name": "Khách sạn An Nhiên Sơn Trà DEMO",
    "city": "Đà Nẵng",
    "checkin_time": null,
    "checkout_time": null,
    "default_language": "vi",
    "supported_languages": "vi,en",
    "latitude": 16.0678,
    "longitude": 108.2467,
    "created_at": "2026-09-23T10:10:13.522744"
  },
  {
    "id": "5f998c96-1bfb-4b70-8a12-cd0e9033e565",
    "slug": "khach-san-vi-du",
    "name": "Khách sạn Ví Dụ",
    "city": "Yên Bái",
    "checkin_time": null,
    "checkout_time": null,
    "default_language": "vi",
    "supported_languages": "vi,en",
    "latitude": null,
    "longitude": null,
    "created_at": "2026-09-24T16:17:24.807945"
  }
]
```

---

### LXXVII. Xem khách sạn (API cũ)
* **Endpoint:** `GET /api/hotels/{slug}`
* **Mô tả:** Chi tiết một khách sạn. Không có → `404`.
* **Phân quyền:** Public.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.

#### Response Mẫu (`200 OK`)
```json
{
  "id": "5f998c96-1bfb-4b70-8a12-cd0e9033e565",
  "slug": "khach-san-vi-du",
  "name": "Khách sạn Ví Dụ",
  "city": "Yên Bái",
  "checkin_time": null,
  "checkout_time": null,
  "default_language": "vi",
  "supported_languages": "vi,en",
  "latitude": null,
  "longitude": null,
  "created_at": "2026-09-24T16:17:24.807945"
}
```

---

### LXXVIII. Tải tài liệu khách sạn (API cũ)
* **Endpoint:** `POST /api/hotels/{slug}/documents`
* **Mô tả:** Tải PDF và nạp chạy nền vào kho Chroma. Không phải PDF → `400`; không có khách sạn → `404`.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.
* **Request Body:** `multipart/form-data`, trường `file` là tệp PDF.

#### Response Mẫu (`200 OK`)
```json
{
  "id": "2291a844-87cd-47fa-8bc8-ce6814d0d648",
  "hotel_id": "5f998c96-1bfb-4b70-8a12-cd0e9033e565",
  "filename": "noi-quy.pdf",
  "content_hash": "a3245b00fa961b5c39029f22f9e6bcdde778254bafb643dab01c9fdc7f26f6e4",
  "status": "processing",
  "error_message": null,
  "chunk_count": 0,
  "uploaded_at": "2026-09-24T16:17:24.825430",
  "processed_at": null
}
```

---

### LXXIX. Danh sách tài liệu khách sạn (API cũ)
* **Endpoint:** `GET /api/hotels/{slug}/documents`
* **Mô tả:** Tài liệu đã tải của khách sạn.
* **Phân quyền:** Public.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.

#### Response Mẫu (`200 OK`)
```json
[
  {
    "id": "2291a844-87cd-47fa-8bc8-ce6814d0d648",
    "hotel_id": "5f998c96-1bfb-4b70-8a12-cd0e9033e565",
    "filename": "noi-quy.pdf",
    "content_hash": "a3245b00fa961b5c39029f22f9e6bcdde778254bafb643dab01c9fdc7f26f6e4",
    "status": "done",
    "error_message": null,
    "chunk_count": 4,
    "uploaded_at": "2026-09-24T16:17:24.825430",
    "processed_at": "2026-09-24T16:17:26.854675"
  }
]
```

---

### LXXX. Xem kết quả tìm tài liệu khách sạn (API cũ)
* **Endpoint:** `POST /api/hotels/{slug}/rag-test`
* **Mô tả:** Các đoạn tài liệu khách sạn tìm được cho một câu hỏi.
* **Phân quyền:** Gửi `X-Admin-Key: <ADMIN_API_KEY>`.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.

#### Request Body
```json
{
  "question": "Giờ trả phòng là mấy giờ?",
  "top_k": 2
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "question": "Giờ trả phòng là mấy giờ?",
  "results": [
    {
      "id": "2291a844-87cd-47fa-8bc8-ce6814d0d648-3-f4e1a851",
      "text": "DEMO DATA - SYNTHETIC - NON-AUTHORITATIVE\nPage 2\nTransport facts\nNhà Gió Mô Phỏng to Bến Gió Xanh: 20-30 minutes; fictional shuttle or walk; exact route is unspecified.\nNhà Gió Mô Phỏng to Xưởng Mây Giấy: 15-25 minutes; fictional shuttle; exact route is unspecified.\nNhà Gió Mô Phỏng to Vòm Mưa: 10-20 minutes; fictional…",
      "metadata": {
        "chunk_index": 3,
        "filename": "noi-quy.pdf",
        "document_id": "2291a844-87cd-47fa-8bc8-ce6814d0d648",
        "hotel_id": "5f998c96-1bfb-4b70-8a12-cd0e9033e565",
        "source": "noi-quy.pdf"
      },
      "distance": 0.7020467519760132,
      "keyword_score": 4.0,
      "vector_rank": 2,
      "source": "noi-quy.pdf"
    },
    {
      "id": "2291a844-87cd-47fa-8bc8-ce6814d0d648-0-b3f4fd13",
      "text": "DEMO DATA - SYNTHETIC - NON-AUTHORITATIVE\nPage 1\n VIETSAGE DEMO FACTS\n Fictional records for local RAG and reading workflow checks\nAll names, prices, hours, policies, schedules, and constraints in this document are synthetic test facts. They do not describe a\nreal hotel or service and contain no map coordinates, review…",
      "metadata": {
        "filename": "noi-quy.pdf",
        "document_id": "2291a844-87cd-47fa-8bc8-ce6814d0d648",
        "chunk_index": 0,
        "source": "noi-quy.pdf",
        "hotel_id": "5f998c96-1bfb-4b70-8a12-cd0e9033e565"
      },
      "distance": 0.5638962984085083,
      "keyword_score": 3.0,
      "vector_rank": 0,
      "source": "noi-quy.pdf"
    }
  ]
}
```

---

### LXXXI. Chat Hotel cũ (JSON)
* **Endpoint:** `POST /api/hotels/{slug}/chat`
* **Mô tả:** Trả lời một lần; hội thoại giữ theo `session_id`.
* **Phân quyền:** Public.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.

#### Request Body
```json
{
  "message": "Giờ trả phòng là mấy giờ?"
}
```

#### Response Mẫu (`200 OK`)
```json
{
  "reply": "Dạ thông tin về giờ trả phòng tôi chưa thấy trong tài liệu, anh/chị có thể hỏi thêm tại quầy lễ tân để được hỗ trợ tốt nhất ạ. Hiện tại, tôi chỉ biết rằng thời gian nhận phòng và trả phòng đều nằm trong khoảng từ 15:00 đến 11:00 mỗi ngày.",
  "session_id": "9eac15d0-4cb1-4672-b977-5b1cb866fca0",
  "sources": []
}
```

---

### LXXXII. Chat Hotel cũ (SSE)
* **Endpoint:** `POST /api/hotels/{slug}/chat/stream`
* **Mô tả:** Trả lời chảy dần: `token`, `done` (`session_id`), `error`. **Lỗi đã biết:** route này hiện luôn trả `500` vì `started` và `prepared` chưa được khai báo ở `app/api/chat.py:250` (phát hiện khi gọi thật ngày 24/09/2026, chưa sửa).
* **Phân quyền:** Public.
* **URL Params:** `slug` *(string, required)*: Mã định danh dạng chữ thường-gạch-ngang.

#### Request Body
```json
{
  "message": "Khách sạn có chỗ đỗ xe không?"
}
```

#### Response thực tế (`500 Internal Server Error`)
Máy chủ trả `Internal Server Error` (xem mô tả).

---

## 4. Bảng mã lỗi HTTP (Error Codes)

| Mã lỗi HTTP | Error Key | Mô tả nguyên nhân | Hướng xử lý |
| :---: | :--- | :--- | :--- |
| `400` | `BAD_REQUEST` | Dữ liệu sai nghiệp vụ: tệp không phải PDF, lý do đánh giá không hợp với rating, `parent_id` không tồn tại. | Đọc `detail` và sửa dữ liệu gửi lên. |
| `401` | `UNAUTHORIZED` | Thiếu hoặc sai khóa admin, khóa đối tác, stay token, token nghiên cứu; sai mật khẩu hoặc PIN. | Kiểm tra header xác thực; đăng nhập hoặc kết nối lại kỳ lưu trú. |
| `403` | `FORBIDDEN` | Thiếu/sai `X-CSRF-Token`; stay token thiếu quyền; chat `HOTEL` khi chưa xác minh lưu trú. | Lấy lại `csrf_token` từ `GET /api/v1/auth/session`; xác minh lại kỳ lưu trú. |
| `404` | `NOT_FOUND` | Không có tài nguyên hoặc tài nguyên không thuộc phiên/đối tác đang gọi; route quản trị khi chưa đặt `ADMIN_API_KEY`. | Kiểm tra lại ID, slug và quyền sở hữu. |
| `409` | `CONFLICT` | Trùng slug hoặc email; dịch vụ cần xác nhận mà `confirmed=false`; thiếu consent khi bật gợi ý chủ động. | Đổi giá trị trùng hoặc gửi lại kèm xác nhận. |
| `410` | `GONE` | `connection_id` của bước kết nối kỳ lưu trú đã hết hạn. | Gọi lại `POST /api/v1/stays/connect`. |
| `422` | `UNPROCESSABLE_ENTITY` | Thiếu trường bắt buộc, sai kiểu, vượt độ dài; PDF không đọc được. | Xem `detail[].loc` để biết trường sai. |
| `429` | `TOO_MANY_REQUESTS` | Sai PIN hoặc sai mật khẩu quá nhiều lần; đối tác vượt số lượt gọi mỗi phút. | Chờ theo header `Retry-After`; lễ tân mở khóa phòng bằng `pin-unlock`. |
| `500` | `INTERNAL_SERVER_ERROR` | Lỗi máy chủ chưa xử lý (hiện có ở `POST /api/hotels/{slug}/chat/stream`). | Xem log máy chủ. |
| `502` | `BAD_GATEWAY` | Câu trả lời lỗi giữa chừng ở `POST /api/v1/partner/conversations/{id}/messages`. | Gửi lại. |
| `503` | `SERVICE_UNAVAILABLE` | Đủ số lượt chat cùng lúc (kèm `Retry-After`); Ollama hoặc cơ sở dữ liệu không kết nối được; route quản trị ghi khi chưa đặt `ADMIN_API_KEY`. | Chờ `Retry-After`; kiểm tra `GET /api/health/ollama`, `GET /api/health/database`. |
