# API chatbot cho website đối tác

Tài liệu cho đội kỹ thuật của website đối tác đã có sẵn giao diện, chỉ cần
chatbot VietSage. Đối tác tự làm khung chat; VietSage trả lời qua API.

## Luồng gọi

```
Trình duyệt của khách ──► Máy chủ của đối tác ──► VietSage API
   (giao diện đối tác)     (giữ khóa bí mật)        (/api/v1/partner/...)
```

- **Chỉ gọi từ máy chủ của đối tác.** Khóa API là bí mật, không được đưa vào
  JavaScript chạy trên trình duyệt, ứng dụng di động hay kho mã công khai.
- Không dùng cookie, không cần CSRF token.
- Mỗi người dùng của đối tác được nhận diện bằng một mã do đối tác tự đặt
  (header `X-Partner-User`). VietSage chỉ lưu dạng băm của mã này.
- Chatbot trả lời về du lịch (chế độ Travel). Dịch vụ phòng khách sạn cần xác
  minh kỳ lưu trú nên không có qua API này.

## 1. Cấp khóa (việc của người vận hành VietSage)

Các lệnh dưới dùng `X-Admin-Key` (biến `ADMIN_API_KEY` trong `.env`). Khóa đối
tác **chỉ hiện một lần** trong kết quả; VietSage chỉ lưu bản băm SHA-256.

```bash
# Tạo đối tác và khóa (mặc định 60 lượt gọi mỗi phút)
curl -X POST https://<máy-chủ-vietsage>/api/v1/admin/partners \
  -H "X-Admin-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"slug": "tour-abc", "name": "Tour ABC", "rate_limit_per_minute": 120}'
# → {"slug": "tour-abc", ..., "key_prefix": "vsk_1a2b3c4d", "api_key": "vsk_1a2b3c4d_..."}

# Xem danh sách (không bao giờ trả lại khóa)
curl https://<máy-chủ-vietsage>/api/v1/admin/partners -H "X-Admin-Key: $ADMIN_API_KEY"

# Đổi khóa: khóa cũ hết hiệu lực ngay, hội thoại cũ vẫn thuộc đối tác
curl -X POST https://<máy-chủ-vietsage>/api/v1/admin/partners/tour-abc/rotate-key -H "X-Admin-Key: $ADMIN_API_KEY"

# Khóa đối tác (mọi lượt gọi trả 401). Đổi khóa sẽ mở lại.
curl -X POST https://<máy-chủ-vietsage>/api/v1/admin/partners/tour-abc/deactivate -H "X-Admin-Key: $ADMIN_API_KEY"
```

Gửi khóa cho đối tác qua kênh an toàn; nếu lộ, đổi khóa ngay.

## 2. Header bắt buộc cho mọi lượt gọi của đối tác

| Header | Giá trị |
|---|---|
| `Authorization` | `Bearer vsk_...` (khóa đối tác) |
| `X-Partner-User` | Mã người dùng của đối tác, 1–128 ký tự, ổn định cho cùng một người (ví dụ id tài khoản, hoặc id phiên nếu khách chưa đăng nhập) |
| `Content-Type` | `application/json` cho các lượt gửi dữ liệu |

Hội thoại chỉ mở được bằng đúng khóa của đối tác đã tạo nó **và** đúng
`X-Partner-User`. Sai một trong hai đều nhận `404`.

## 3. Các endpoint

| Phương thức | Đường dẫn | Việc |
|---|---|---|
| `POST` | `/api/v1/partner/conversations` | Tạo hội thoại mới |
| `POST` | `/api/v1/partner/conversations/{id}/messages` | Gửi tin, nhận **cả câu trả lời một lần** (JSON) |
| `POST` | `/api/v1/partner/conversations/{id}/messages/stream` | Gửi tin, nhận câu trả lời **chảy dần** (SSE) |
| `GET` | `/api/v1/partner/conversations/{id}/messages` | Lịch sử (tối đa 200 tin gần nhất, cũ → mới) |
| `DELETE` | `/api/v1/partner/conversations/{id}` | Xóa hội thoại và mọi dữ liệu kèm theo |

Đặc tả máy đọc được: `GET /openapi.json` (nhóm `partner-api`).

### Tạo hội thoại

```http
POST /api/v1/partner/conversations
{"locale": "vi", "destination_slug": null}
```

`locale`: `vi`, `en` hoặc `zh-Hans`. `destination_slug` (tùy chọn) khoanh câu
trả lời vào một điểm đến. Kết quả `201`:
`{"id": "…", "locale": "vi", "destination_slug": null, "created_at": "…"}`.

### Gửi tin — nhận JSON một lần

```http
POST /api/v1/partner/conversations/{id}/messages
{"message": "Hồ Thác Bà có gì chơi?"}
```

Trường tùy chọn: `locale` (đổi ngôn ngữ từ lượt này), `destination_slug`
(`null` để bỏ khoanh vùng), `currentArtifact` (xem mục 4). Tối đa 4000 ký tự.

```json
{
  "conversation_id": "…",
  "message_id": 128,
  "reply": "Ở Hồ Thác Bà bạn có thể …",
  "suggestions": [{"id": "context-duration", "label": "Thêm thời lượng chuyến đi", "prompt": "…"}],
  "places": [],
  "action": null,
  "artifact_id": null,
  "artifact_version": null
}
```

- `reply` là Markdown đơn giản (in đậm, danh sách).
- `suggestions`: nút gợi ý; khi khách bấm, gửi `prompt` làm tin nhắn tiếp theo.
- `places`: thẻ địa điểm (có thể rỗng).
- `action`: có khi chatbot lập **lịch trình/kế hoạch có cấu trúc**
  (`type = "GENERATIVE_UI"`); `action.artifact` chứa ngày, giờ, hoạt động để
  đối tác tự vẽ.

### Gửi tin — nhận chảy dần (SSE)

Cùng thân yêu cầu, trả `text/event-stream`. Các sự kiện:

| Sự kiện | Dữ liệu | Ý nghĩa |
|---|---|---|
| `token` | `{"text": "…"}` | Nối thêm vào câu trả lời |
| `replace` | `{"text": "…"}` | Thay toàn bộ câu trả lời (câu trả lời soạn sẵn) |
| `action` | như `action` ở trên | Lịch trình/kế hoạch có cấu trúc |
| `done` | `conversation_id`, `message_id`, `suggestions`, tùy chọn `places`, `artifact_id`, `artifact_version` | Kết thúc lượt |
| `error` | `{"detail": "Không thể hoàn tất câu trả lời."}` | Lỗi giữa chừng; lượt không được lưu |

## 4. Sửa lịch trình bằng lời

Khi khách đang xem lịch trình (`action.artifact`) và gõ "đổi ngày 2 sang buổi
chiều", gửi kèm nguyên văn lịch trình đó:

```json
{"message": "đổi ngày 2 sang buổi chiều", "currentArtifact": { …artifact đang hiển thị… }}
```

VietSage kiểm tra lại, trả `action` mới với phiên bản tăng 1. Nếu không sửa
được, lịch trình cũ được giữ nguyên và `reply` giải thích.

## 5. Mã lỗi

| Mã | Khi nào | Cách xử lý |
|---|---|---|
| `400` | Thiếu hoặc sai `X-Partner-User` | Sửa header |
| `401` | Khóa thiếu, sai, đã đổi hoặc đối tác bị khóa | Kiểm tra khóa; liên hệ VietSage |
| `404` | Hội thoại không tồn tại hoặc không thuộc người dùng/đối tác này | Tạo hội thoại mới |
| `422` | Thân yêu cầu sai (trường lạ, quá 4000 ký tự, `mode` …) | Sửa yêu cầu |
| `429` | Vượt giới hạn lượt gọi mỗi phút của đối tác | Chờ `Retry-After` giây |
| `502` | Lỗi giữa chừng (chỉ endpoint JSON) | Thử lại |
| `503` | VietSage đang phục vụ tối đa số lượt chat cùng lúc | Chờ `Retry-After` giây |

## 6. Mã mẫu

### Node.js 18+ (chuyển tiếp chảy dần về trình duyệt)

```js
// Máy chủ đối tác: POST /chat  {conversationId?, message}  → chuyển tiếp SSE của VietSage.
import http from "node:http";

const VIETSAGE = process.env.VIETSAGE_URL;          // ví dụ https://api.vietsage.vn
const KEY = process.env.VIETSAGE_PARTNER_KEY;       // chỉ nằm ở máy chủ

async function vietsage(path, userId, body) {
  return fetch(`${VIETSAGE}/api/v1/partner${path}`, {
    method: "POST",
    headers: {Authorization: `Bearer ${KEY}`, "X-Partner-User": userId, "Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
}

http.createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/chat") { res.writeHead(404).end(); return; }
  let raw = ""; for await (const chunk of req) raw += chunk;
  const {conversationId, message} = JSON.parse(raw);
  const userId = "user-123";                        // lấy từ phiên đăng nhập của website đối tác
  let id = conversationId;
  if (!id) id = (await (await vietsage("/conversations", userId, {locale: "vi"})).json()).id;
  const upstream = await vietsage(`/conversations/${id}/messages/stream`, userId, {message});
  res.writeHead(upstream.status, {"Content-Type": upstream.headers.get("content-type") || "application/json",
                                  "Cache-Control": "no-cache", "X-Conversation-Id": id});
  for await (const chunk of upstream.body) res.write(chunk);
  res.end();
}).listen(3000);
```

Trình duyệt đọc luồng bằng `fetch(...).body.getReader()` và tách từng khối
`event:`/`data:` ngăn bởi dòng trống.

### Python (nhận JSON một lần)

```python
import os
import httpx

VIETSAGE = os.environ["VIETSAGE_URL"]
HEADERS = {"Authorization": f"Bearer {os.environ['VIETSAGE_PARTNER_KEY']}"}

def ask(user_id: str, message: str, conversation_id: str | None = None) -> dict:
    headers = {**HEADERS, "X-Partner-User": user_id}
    with httpx.Client(base_url=f"{VIETSAGE}/api/v1/partner", headers=headers, timeout=180) as client:
        if conversation_id is None:
            conversation_id = client.post("/conversations", json={"locale": "vi"}).raise_for_status().json()["id"]
        answer = client.post(f"/conversations/{conversation_id}/messages", json={"message": message})
        return answer.raise_for_status().json()
```

## 7. Lưu ý vận hành

- Giới hạn lượt gọi tính trên **mỗi tiến trình máy chủ**; chạy nhiều worker thì
  trần thực tế nhân lên theo số worker. Giới hạn số lượt chat cùng lúc của cả hệ
  thống (`CHAT_MAX_CONCURRENT_REQUESTS`) vẫn áp dụng cho mọi đối tác và PWA.
- Câu trả lời lập lịch trình mất khoảng 9 giây với model 3B chạy tại máy; đặt
  thời gian chờ phía đối tác tối thiểu 180 giây.
- Đối tác phải thông báo cho khách rằng nội dung chat được gửi tới VietSage, và
  gọi `DELETE` khi khách yêu cầu xóa dữ liệu hoặc xóa tài khoản.
- Không đưa thông tin cá nhân (họ tên, số điện thoại, email) vào
  `X-Partner-User`; dùng id nội bộ.
