"""Ollama, database, chat, and embedding health endpoints.

The GET checks are cheap reads and stay public so an operator or a load
balancer can ask "is it up?". The two POST probes run a real inference on the
GPU, so they are operator-only: left open, anyone holding the demo URL could
stall the machine, which falls over at about 15 concurrent requests.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from app.core.database import engine
from app.core.security import require_configured_admin
from app.services.llm_client import ollama_client

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live")
async def check_live():
    """Process liveness; independent from database and model providers."""
    return {"status": "ok", "app": "Chatbot VietSage"}


@router.get("/database")
def check_database():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "database_running": True,
            "backend": engine.dialect.name,
            "pgvector_enabled": engine.dialect.name == "postgresql",
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/ollama")
async def check_ollama():
    """Kiểm tra Ollama có đang chạy, có đủ model chat + embed không."""
    try:
        result = await ollama_client.health_check()
        return result
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=(
                f"Không kết nối được tới Ollama. "
                f"Hãy chắc chắn đã chạy `ollama serve` và pull model. "
                f"Lỗi gốc: {str(e)}"
            ),
        )


@router.get("/provider")
async def check_active_provider():
    """Report the Ollama service used for both chat and embeddings."""
    provider = ollama_client.provider_name
    status = {"active_provider": provider}

    try:
        ollama_status = await ollama_client.health_check()
        status["ollama"] = ollama_status
    except Exception as e:  # noqa: BLE001
        status["ollama"] = {"ollama_running": False, "error": str(e)}

    return status


class ChatTestRequest(BaseModel):
    message: str


@router.post("/chat-test", dependencies=[Depends(require_configured_admin)])
async def chat_test(req: ChatTestRequest):
    """Test nhanh 1 lượt chat, không streaming, không RAG, không lịch sử."""
    try:
        reply = await ollama_client.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Bạn là VietSage, trợ lý du lịch Việt Nam. "
                        "Trả lời ngắn gọn, thân thiện, bằng đúng ngôn ngữ người dùng."
                    ),
                },
                {"role": "user", "content": req.message},
            ]
        )
        return {"reply": reply, "provider": ollama_client.provider_name}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(e))


class EmbedTestRequest(BaseModel):
    text: str


@router.post("/embed-test", dependencies=[Depends(require_configured_admin)])
async def embed_test(req: EmbedTestRequest):
    """Test nhanh tạo embedding cho một đoạn text bằng Ollama."""
    try:
        vector = await ollama_client.embed(req.text)
        return {"dimension": len(vector), "sample": vector[:5]}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(e))
