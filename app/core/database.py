"""
Kết nối database (SQLite ở giai đoạn test, sẽ đổi sang PostgreSQL khi lên server thật
— chỉ cần đổi DATABASE_URL trong .env, code không cần sửa).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

# check_same_thread=False cần thiết vì FastAPI xử lý request trên nhiều thread
# (chỉ áp dụng khi dùng SQLite; PostgreSQL không cần flag này)
connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=not settings.database_url.startswith("sqlite"),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """Dependency injection cho FastAPI — mở session, đóng session sau khi xong."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
