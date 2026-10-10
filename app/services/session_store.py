"""
Quản lý session chat NGẮN HẠN, lưu hoàn toàn trong bộ nhớ (RAM) — không ghi
xuống database, đúng theo yêu cầu "không tài khoản, không lưu dài hạn".

Mỗi session (1 lượt khách chat) lưu:
- Lịch sử vài tin nhắn gần nhất (để chatbot nhớ ngữ cảnh trong cuộc hội thoại)
- Tập hợp id các chunk đã từng lấy ra gợi ý cho khách này rồi (để tránh lặp)

LƯU Ý: vì lưu trong RAM, dữ liệu sẽ mất khi restart server — đúng chủ đích,
không phải bug. Session tự "hết hạn" (dọn dẹp) sau một khoảng thời gian
không hoạt động, tránh rò rỉ bộ nhớ khi chạy lâu dài.
"""
import time
import uuid

# Số lượt hỏi-đáp gần nhất được giữ lại làm ngữ cảnh cho LLM.
# Giữ 3 lượt (= 6 tin nhắn) để tiết kiệm token/tốc độ trên GPU 6GB.
# Tăng lên 4-6 nếu chạy trên Cloud với GPU mạnh hơn.
MAX_HISTORY_TURNS = 3

# Session không hoạt động quá thời gian này (giây) sẽ bị dọn dẹp khi có
# session mới được tạo/truy cập (dọn dẹp kiểu "lazy", không cần chạy nền riêng).
SESSION_TTL_SECONDS = 2 * 60 * 60  # 2 tiếng


class ChatSession:
    def __init__(self, session_id: str, hotel_id: str) -> None:
        self.session_id = session_id
        self.hotel_id = hotel_id
        self.history: list[dict] = []  # [{"role": "user"/"assistant", "content": str}]
        self.shown_chunk_ids: set[str] = set()
        self.last_active: float = time.time()

    def add_turn(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        # Chỉ giữ lại N lượt gần nhất (1 lượt = 1 user + 1 assistant = 2 message)
        max_messages = MAX_HISTORY_TURNS * 2
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]
        self.last_active = time.time()

    def mark_shown(self, chunk_ids: list[str]) -> None:
        self.shown_chunk_ids.update(chunk_ids)
        self.last_active = time.time()


class SessionStore:
    def __init__(self) -> None:
        # Cùng một session_id không được dùng chung giữa hai khách sạn.
        self._sessions: dict[tuple[str, str], ChatSession] = {}

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_active > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            del self._sessions[sid]

    def get_or_create(
        self, hotel_id: str, session_id: str | None
    ) -> ChatSession:
        self._cleanup_expired()

        if session_id and (hotel_id, session_id) in self._sessions:
            session = self._sessions[(hotel_id, session_id)]
            # Bảo vệ: session_id không được dùng chéo giữa các khách sạn khác nhau
            if session.hotel_id == hotel_id:
                session.last_active = time.time()
                return session

        new_id = session_id or str(uuid.uuid4())
        session = ChatSession(session_id=new_id, hotel_id=hotel_id)
        self._sessions[(hotel_id, new_id)] = session
        return session


session_store = SessionStore()
