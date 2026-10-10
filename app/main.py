"""FastAPI entry point for Chatbot VietSage."""
import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import auth, destinations, health, operations, partner, proactive, travel_chat, travel_documents, unified
from app.core.config import (
    cors_startup_notice,
    origins_are_open,
    parse_allowed_origins,
    settings,
)
from app.core.database import SessionLocal
from app.core.schema import initialize_database
from app.services.destination_catalog import ensure_initial_destinations
from app.services.llm_client import llm_client
from app.services.chat_metrics import prune_chat_logs
from app.services.travel_ingestion import run_pending_jobs

# Import models so SQLAlchemy can create tables.
from app.models import (  # noqa: F401
    destination,
    hotel,
    knowledge,
    travel_document,
    unified as unified_models,
    auth as auth_models,
    partner as partner_models,
)

if settings.hotel_mode_enabled:
    from app.models import hotel_document, service_mention  # noqa: F401

STATIC_DIR = Path(__file__).parent / "static"
PWA_DIR = STATIC_DIR / "pwa"
LEGACY_UI_DIR = STATIC_DIR / "legacy"
OPERATIONS_UI = STATIC_DIR / "operations" / "index.html"
PWA_ASSET_FILES = {
    "styles/foundation.css": "text/css",
    "styles/explore.css": "text/css",
    "styles/application.css": "text/css",
    "scripts/bootstrap.js": "application/javascript",
    "scripts/auth.js": "application/javascript",
    "scripts/content-catalog.js": "application/javascript",
    "scripts/markdown-renderer.js": "application/javascript",
    "scripts/sse-client.js": "application/javascript",
    "scripts/generative-ui.js": "application/javascript",
    "scripts/message-feedback.js": "application/javascript",
    "scripts/pin-input.js": "application/javascript",
    "scripts/place-cards.js": "application/javascript",
    "styles/place-cards.css": "text/css",
    "scripts/motion.js": "application/javascript",
    "scripts/mood-switch.js": "application/javascript",
    "styles/motion.css": "text/css",
    "scripts/proactive-recommendations.js": "application/javascript",
    "assets/brand/vietsage-lockup.svg": "image/svg+xml",
    "assets/explore/ninh-binh-desktop.webp": "image/webp",
    "assets/explore/ninh-binh-mobile.webp": "image/webp",
    "assets/explore/hoa-bac-desktop.webp": "image/webp",
    "assets/explore/hoa-bac-mobile.webp": "image/webp",
    "assets/explore/nho-que-desktop.webp": "image/webp",
    "assets/explore/nho-que-mobile.webp": "image/webp",
    "assets/explore/hoi-an-desktop.webp": "image/webp",
    "assets/explore/hoi-an-mobile.webp": "image/webp",
    "assets/explore/hanoi-desktop.webp": "image/webp",
    "assets/explore/hanoi-mobile.webp": "image/webp",
    "assets/explore/con-dao-desktop.webp": "image/webp",
    "assets/explore/con-dao-mobile.webp": "image/webp",
}
logger = logging.getLogger(__name__)
ingestion_worker_task: asyncio.Task[None] | None = None

app = FastAPI(
    title="Chatbot VietSage API",
    description="Trợ lý du lịch và lưu trú thống nhất, dùng hybrid RAG.",
    version="0.3.0",
)


async def _run_ingestion_worker() -> None:
    try:
        await run_pending_jobs(stop_when_empty=False)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Ingestion worker stopped unexpectedly.")


@app.on_event("startup")
async def on_startup():
    global ingestion_worker_task
    initialize_database()
    retention = prune_chat_logs()
    if retention["removed"]:
        logger.info(
            "Applied chat log retention: removed %s rows, kept %s rows.",
            retention["removed"],
            retention["kept"],
        )
    db = SessionLocal()
    try:
        ensure_initial_destinations(db)
        if settings.unified_demo_enabled and settings.app_env != "production":
            from app.services.demo_seed import seed_unified_demo

            seed_unified_demo(db)
    finally:
        db.close()
    await llm_client.startup()
    if settings.ollama_warmup_enabled:
        try:
            await llm_client.warmup()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ollama warmup failed; API will continue: %s", exc)
    if settings.ingestion_worker_enabled:
        ingestion_worker_task = asyncio.create_task(
            _run_ingestion_worker(), name="vietsage-ingestion-worker"
        )



@app.on_event("shutdown")
async def on_shutdown():
    if ingestion_worker_task is not None:
        ingestion_worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await ingestion_worker_task
    await llm_client.shutdown()


_origins = parse_allowed_origins(settings.allowed_origins)

# A wildcard lets any page on the internet call every endpoint from a visitor's
# browser, using that visitor's network position. Say so where an operator will
# actually see it rather than leaving it to a code review.
_cors_notice = cors_startup_notice(_origins, settings.app_env)
if _cors_notice is not None:
    _level, _message = _cors_notice
    getattr(logger, _level)(_message)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # The PWA is same-origin. Credentialed CORS is enabled only for an
    # explicit allowlist, never together with a wildcard origin. Asking the
    # parsed list rather than the raw string also catches "a.example,*", which
    # CORSMiddleware honours as a wildcard.
    allow_credentials=not origins_are_open(_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(destinations.router)
app.include_router(travel_documents.router)
app.include_router(travel_chat.router)
app.include_router(unified.router)
app.include_router(partner.router)
app.include_router(proactive.router)
app.include_router(auth.router)
app.include_router(operations.router)

if settings.hotel_mode_enabled:
    # Importing these modules initializes the legacy Chroma hotel store, so do
    # it only when the feature is explicitly enabled.
    from app.api import chat, documents, hotels, rag_test

    app.include_router(hotels.router)
    app.include_router(documents.router)
    app.include_router(rag_test.router)
    app.include_router(chat.router)


@app.get("/chat-ui")
async def chat_ui(hotel: str | None = None):
    if hotel and settings.hotel_mode_enabled:
        return FileResponse(LEGACY_UI_DIR / "hotel-chat.html")
    return FileResponse(LEGACY_UI_DIR / "travel-chat.html")


@app.get("/")
@app.get("/app")
async def unified_ui():
    return FileResponse(PWA_DIR / "index.html")


@app.get("/operations", include_in_schema=False)
async def operations_ui():
    if not settings.admin_api_key:
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(OPERATIONS_UI)


@app.get("/manifest.webmanifest")
async def web_manifest():
    return FileResponse(PWA_DIR / "manifest.webmanifest")


@app.get("/sw.js")
async def service_worker():
    return FileResponse(PWA_DIR / "service-worker.js", media_type="application/javascript")


@app.get("/app-assets/{asset_path:path}", include_in_schema=False)
async def pwa_asset(asset_path: str):
    media_type = PWA_ASSET_FILES.get(asset_path)
    if media_type is None:
        raise HTTPException(status_code=404, detail="PWA asset not found")
    return FileResponse(
        PWA_DIR / asset_path,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


if settings.hotel_mode_enabled:
    @app.get("/hotel-chat")
    async def hotel_chat_ui():
        """Optional hotel-scoped UI."""
        return FileResponse(LEGACY_UI_DIR / "hotel-chat.html")


@app.get("/api/info")
async def api_info():
    return {
        "status": "ok",
        "app": "Chatbot VietSage",
        # This is the only public source of the runtime banner. It is explicit
        # configuration, never inferred from URL or client state.
        "data_mode": settings.data_mode if settings.app_env != "production" else "real",
        "hotel_mode_enabled": settings.hotel_mode_enabled,
        "unified_demo_enabled": settings.unified_demo_enabled and settings.app_env != "production",
        "default_country": settings.default_country_name,
        "message": "Chatbot VietSage đang chạy. Xem /docs để kiểm thử.",
    }
