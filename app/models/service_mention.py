"""
Thống kê số lần 1 dịch vụ/địa điểm được khách quan tâm/chọn tại 1 khách sạn.
Dùng để rank độ phổ biến khi gợi ý (bước sau). category để trống được —
data hiện tại của bạn chưa gắn nhãn, sẽ bổ sung dần hoặc suy luận bằng LLM sau.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class ServiceMention(Base):
    __tablename__ = "service_mentions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"), index=True)

    service_name: Mapped[str] = mapped_column(String, index=True)

    # Có thể để trống — chưa cần phân loại ngay từ đầu
    category: Mapped[str] = mapped_column(String, nullable=True)

    count: Mapped[int] = mapped_column(Integer, default=1)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
