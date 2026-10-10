"""
Main hotel chat endpoints.

/chat keeps the existing JSON response. /chat/stream adds SSE streaming for clients
that want to render the answer character by character.
"""
import json
import re
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.models.hotel import Hotel
from app.models.hotel_document import HotelDocument
from app.models.service_mention import ServiceMention
from app.models.schemas import ChatRequest, ChatResponse
from app.services.llm_client import ollama_client
from app.services.chat_metrics import observe_sse_stream
from app.services.session_store import session_store
from app.services.vector_store import vector_store

router = APIRouter(prefix="/api/hotels", tags=["chat"])

# Số chunk tài liệu truy xuất cho mỗi câu hỏi.
TOP_K_CONTEXT = 4

# Danh mục các dịch vụ phổ biến của khách sạn với Regex đã compile sẵn
SERVICE_PATTERNS = {
    "Ăn sáng / Nhà hàng": {
        "pattern": re.compile(r"ăn sáng|breakfast|buffet|nhà hàng|restaurant|điểm tâm|đồ ăn", re.IGNORECASE),
        "category": "Ẩm thực"
    },
    "Hồ bơi": {
        "pattern": re.compile(r"hồ bơi|bể bơi|pool|swimming|tắm", re.IGNORECASE),
        "category": "Giải trí"
    },
    "Spa / Massage": {
        "pattern": re.compile(r"spa|massage|xông hơi|trị liệu", re.IGNORECASE),
        "category": "Chăm sóc sức khỏe"
    },
    "Nhận/Trả phòng": {
        "pattern": re.compile(r"checkin|nhận phòng|checkout|trả phòng|nhận bàn giao|trả bàn giao", re.IGNORECASE),
        "category": "Dịch vụ phòng"
    },
    "Giặt ủi": {
        "pattern": re.compile(r"giặt ủi|giặt là|laundry|giặt quần áo", re.IGNORECASE),
        "category": "Dịch vụ phòng"
    },
    "Vận chuyển / Đưa đón": {
        "pattern": re.compile(r"xe đưa đón|thuê xe|shuttle|taxi|đưa đón sân bay|đặt xe|bus", re.IGNORECASE),
        "category": "Vận chuyển"
    },
    "Wifi / Internet": {
        "pattern": re.compile(r"wifi|mạng|internet|pass wifi|mật khẩu wifi", re.IGNORECASE),
        "category": "Tiện ích"
    },
    "Phòng tập Gym": {
        "pattern": re.compile(r"phòng tập|gym|fitness|thể hình", re.IGNORECASE),
        "category": "Giải trí"
    },
    "Quầy bar": {
        "pattern": re.compile(r"quầy bar|bar|lounge|đồ uống", re.IGNORECASE),
        "category": "Ẩm thực"
    }
}


def record_service_mentions(hotel_id: str, message: str) -> None:
    """Quét từ khóa trong tin nhắn bằng Pre-compiled Regex và cập nhật lượt nhắc ngầm (Background Task)."""
    matched_services = [
        (service_name, config["category"])
        for service_name, config in SERVICE_PATTERNS.items()
        if config["pattern"].search(message)
    ]
    if not matched_services:
        return

    db = SessionLocal()
    try:
        matched_names = [s[0] for s in matched_services]
        existing_mentions = {
            m.service_name: m
            for m in db.query(ServiceMention).filter(
                ServiceMention.hotel_id == hotel_id,
                ServiceMention.service_name.in_(matched_names)
            ).all()
        }

        for service_name, category in matched_services:
            if service_name in existing_mentions:
                existing_mentions[service_name].count += 1
            else:
                db.add(ServiceMention(
                    hotel_id=hotel_id,
                    service_name=service_name,
                    category=category,
                    count=1
                ))
        db.commit()
    except Exception as e:
        print(f"Lỗi lưu Service Mention: {e}")
    finally:
        db.close()


def _attach_filenames(context_chunks: list[dict], db: Session) -> None:
    document_ids = {
        (chunk.get("metadata") or {}).get("document_id")
        for chunk in context_chunks
        if (chunk.get("metadata") or {}).get("document_id")
    }
    if not document_ids:
        return

    docs = db.query(HotelDocument).filter(HotelDocument.id.in_(document_ids)).all()
    filename_by_id = {doc.id: doc.filename for doc in docs}
    for chunk in context_chunks:
        metadata = chunk.get("metadata") or {}
        document_id = metadata.get("document_id")
        filename = filename_by_id.get(document_id)
        if filename:
            metadata["filename"] = filename
            metadata["source"] = filename
            chunk["metadata"] = metadata
            chunk["source"] = filename


def _build_system_prompt(hotel: Hotel, context_chunks: list[dict]) -> str:
    """Build an engaging RAG prompt with subtle upselling & hospitality rules."""
    if context_chunks:
        context_parts = []
        for idx, chunk in enumerate(context_chunks, start=1):
            context_parts.append(f"[{idx}] {chunk['text']}")
        context_text = "\n---\n".join(context_parts)
    else:
        context_text = "(Khong tim thay thong tin chi tiet trong tai lieu.)"

    city_text = f" tại {hotel.city}" if hotel.city else ""
    return (
        f"Bạn là Đại sứ Chăm sóc Khách hàng nhiệt thành, tinh tế của khách sạn '{hotel.name}'{city_text}.\n"
        "PHONG CÁCH PHẢN HỒI:\n"
        "- Trả lời bằng tiếng Việt ấm áp, sinh động, lịch sự và cuốn hút như một người địa phương sành điệu.\n"
        "- Trình bày mạch lạc, có cấu trúc rõ ràng, tư vấn nhiệt tình các điểm đến hoặc dịch vụ.\n\n"
        "XỬ LÝ NGÂN SÁCH & GIÁ CẢ:\n"
        "- Nếu khách đưa ra ngân sách/số tiền cụ thể (ví dụ: 'chỉ có 20k', 'dưới 50k'), hãy rà soát TOÀN BỘ CONTEXT để liệt kê TẤT CẢ các điểm tham quan hoặc dịch vụ có mức giá KHÔNG VƯỢT QUÁ số tiền đó (bao gồm các điểm Miễn phí và các điểm có giá <= ngân sách).\n"
        "- Ghi rõ mức giá chính xác của từng điểm nếu có trong CONTEXT.\n\n"
        "NGUYÊN TẮC GỢI Ý DẪN KHÁCH CHI TIÊU (UPSALE TINH TẾ):\n"
        "1. Đúng nhu cầu & Đúng thời điểm: Sau khi giải đáp đúng ngân sách/yêu cầu của khách, hãy khéo léo gợi ý thêm 1 trải nghiệm phù hợp (ví dụ: tư vấn thêm điểm miễn phí đẹp, xe đưa đón tiết kiệm, hoặc dịch vụ khách sạn).\n"
        "2. Minh bạch giá: Nêu rõ giá hoặc miễn phí.\n"
        "3. Dễ dàng từ chối: Sử dụng câu từ nhẹ nhàng, tôn trọng lựa chọn của khách, không gây áp lực.\n\n"
        "RÀNG BUỘC THÔNG TIN:\n"
        "- Chỉ dùng thông tin có trong CONTEXT bên dưới. Không tự bịa đặt chi tiết không có sẵn.\n"
        "- Nếu CONTEXT không đủ thông tin, hãy trả lời lịch sự: 'Dạ thông tin này em chưa thấy trong tài liệu, anh/chị có thể hỏi thêm tại quầy lễ tân để được hỗ trợ tốt nhất ạ.'\n\n"
        f"CONTEXT TÀI LIỆU:\n{context_text}"
    )


async def _prepare_chat(slug: str, payload: ChatRequest, db: Session):
    hotel = db.query(Hotel).filter(Hotel.slug == slug).first()
    if not hotel:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách sạn.")

    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Tin nhan khong duoc de trong.")

    session = session_store.get_or_create(hotel.id, payload.session_id)

    # Always retrieve the best current context. Avoid excluding previously shown chunks;
    # follow-up questions often need the same source again.
    context_chunks = await vector_store.query(
        hotel_id=hotel.id,
        query_text=payload.message,
        top_k=TOP_K_CONTEXT,
    )
    _attach_filenames(context_chunks, db)

    system_prompt = _build_system_prompt(hotel, context_chunks)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(session.history)
    messages.append({"role": "user", "content": payload.message})
    return session, context_chunks, messages


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{slug}/chat", response_model=ChatResponse)
async def chat(
    slug: str,
    payload: ChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    session, context_chunks, messages = await _prepare_chat(slug, payload, db)
    prepared = time.perf_counter()

    try:
        reply = await ollama_client.chat(messages=messages)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Không gọi được LLM (Ollama): {str(e)}") from e

    session.add_turn("user", payload.message)
    session.add_turn("assistant", reply)
    session.mark_shown([c["id"] for c in context_chunks])

    # Ghi nhận lượt nhắc dịch vụ ngầm (asynchronous background task)
    background_tasks.add_task(record_service_mentions, session.hotel_id, payload.message)

    return ChatResponse(reply=reply, session_id=session.session_id, sources=[])


@router.post("/{slug}/chat/stream")
async def chat_stream(
    slug: str,
    payload: ChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    session, context_chunks, messages = await _prepare_chat(slug, payload, db)

    async def event_generator() -> AsyncGenerator[str, None]:
        reply_parts: list[str] = []
        try:
            async for chunk in ollama_client.chat_stream(messages=messages):
                reply_parts.append(chunk)
                # Thay đổi: stream từng chunk text thay vì chạy for char in chunk để tối ưu hiệu năng
                yield _sse("token", {"text": chunk})

            reply = "".join(reply_parts)
            session.add_turn("user", payload.message)
            session.add_turn("assistant", reply)
            session.mark_shown([c["id"] for c in context_chunks])

            yield _sse("done", {"session_id": session.session_id})
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"detail": f"Không gọi được LLM (Ollama): {str(e)}"})

    # Ghi nhận lượt nhắc dịch vụ ngầm (asynchronous background task)
    background_tasks.add_task(record_service_mentions, session.hotel_id, payload.message)

    return StreamingResponse(
        observe_sse_stream(
            event_generator(),
            started=started,
            prepared=prepared,
            scope=slug,
            route="hotel",
            question=payload.message,
            session_id=session.session_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
