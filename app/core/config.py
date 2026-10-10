"""
Cau hinh chung cho toan bo ung dung.
Doc tu file .env (xem .env.example de biet cac bien can thiet).
"""
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Ollama ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "qwen3.5:9b-q4_K_M"
    ollama_embed_model: str = "qwen3-embedding:0.6b"
    ollama_keep_alive: str = "1h"
    ollama_embed_keep_alive: str = "0"
    ollama_embed_batch_size: int = 32
    ollama_warmup_enabled: bool = True

    # Giới hạn cửa sổ ngữ cảnh (context window) của Ollama.
    # Mặc định Ollama cấp phát 8192 token, rất tốn VRAM.
    # Giảm xuống 2048 để tiết kiệm ~1GB VRAM trên GPU 6GB.
    # Tăng lên 4096-8192 khi chạy Cloud với GPU mạnh.
    ollama_num_ctx: int = 4096
    # Short factual turns do not need the full KV cache. Long requests keep
    # ``ollama_num_ctx`` so itinerary answers retain enough room.
    ollama_short_num_ctx: int = 2048
    ollama_temperature: float = 0.1
    ollama_num_predict: int = 1024
    # Long-answer requests may use this larger budget only when the estimated
    # input still leaves room inside the configured context window.
    ollama_long_num_predict: int = 1536
    ollama_context_reserve_tokens: int = 128
    ollama_think: bool = False

    # Shared HTTP pool. Reusing connections materially reduces latency for
    # repeated chat and embedding requests on a hotel workstation.
    api_timeout_seconds: float = 300.0
    api_connect_timeout_seconds: float = 10.0
    api_max_connections: int = 20
    # Process-local guard for the one-worker local/ngrok deployment. A
    # multi-worker production service must enforce the same ceiling at a
    # shared edge or datastore boundary.
    chat_max_concurrent_requests: int = Field(default=10, ge=1)
    chat_retry_after_seconds: int = Field(default=5, ge=1)

    # --- RAG retrieval ---
    rag_max_vector_distance: float = 0.56
    rag_keyword_min_score: float = 1.0
    travel_top_k_context: int = 4
    travel_collection_name: str = "travel_documents_v1"
    embedding_dimensions: int = 1024
    embedding_query_cache_size: int = 128

    # --- Travel defaults ---
    default_country_code: str = "VN"
    default_country_name: str = "Việt Nam"
    travel_data_root: str = "./data"
    source_storage_dir: str = "./runtime/sources"

    # --- Place-card catalogue (D1/D4) ---
    # Swapping demo data for a real feed is a configuration change: point this
    # at a validated file (see datasets/README.md) and nothing else changes.
    # The loader fails closed -- missing file, unreadable JSON, or any
    # media/review entry without complete licence flags disables cards
    # entirely rather than guessing a default licence.
    catalogue_path: str = "datasets/demo/catalogue.json"
    catalogue_enabled: bool = True

    # Public runtime label. Real is the safe default; demo is opt-in through
    # BAYAH_DATA_MODE=demo and is intended to be paired with isolated storage.
    data_mode: Literal["real", "demo"] = Field(
        default="real",
        validation_alias=AliasChoices("BAYAH_DATA_MODE", "DATA_MODE"),
    )

    # --- Database ---
    database_url: str = "sqlite:///./data/vietsage.db"
    chroma_persist_dir: str = "./data/chroma"

    # --- Durable ingestion ---
    ingestion_poll_seconds: float = 2.0
    ingestion_max_attempts: int = 3
    ingestion_job_lease_seconds: int = Field(default=300, ge=30)
    ingestion_job_heartbeat_seconds: int = Field(default=30, ge=5)
    ingestion_worker_enabled: bool = False
    admin_api_key: str = ""
    # Legacy hotel routes/UI are kept behind a switch and are disabled for the
    # current Chatbot VietSage product.
    hotel_mode_enabled: bool = False

    # Unified Travel/Hotel MVP. Demo data is enabled locally and explicitly
    # disabled in production deployment configuration.
    unified_demo_enabled: bool = True
    stay_access_ttl_hours: int = 8
    privacy_policy_version: str = "demo-1.0"

    # Availability never activates a session. All new sessions start OFF.
    proactive_research_enabled: bool = False
    proactive_ui_toggle_enabled: bool = True
    proactive_fixture_enabled: bool = False
    proactive_shadow_enabled: bool = False

    # Opt-in performance/content log. Keep disabled unless an operator is
    # actively measuring because records contain guest questions and answers.
    chat_log_enabled: bool = False
    chat_log_dir: str = "./runtime/logs"
    chat_log_retention_days: int = Field(default=30, ge=1)
    # Metadata-only performance records remain separate from the opt-in raw log.
    chat_telemetry_enabled: bool = False

    # --- App ---
    app_env: str = "development"
    # Cross-origin callers, comma separated. The PWA is served by this same
    # application, so same-origin use (including an ngrok tunnel) never needs
    # an entry here. The default therefore only covers a local dev front end
    # running on a different port. "*" is still accepted but is logged loudly
    # at startup because it exposes every endpoint to any web page.
    allowed_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # Durable browser identity. Tokens are random and only their hashes are stored.
    auth_cookie_name: str = "vietsage_session"
    csrf_cookie_name: str = "vietsage_csrf"
    auth_session_ttl_hours: int = 720
    auth_secret: str = "development-only-change-me"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_http_timeout_seconds: float = 10.0
    auth_login_max_attempts: int = 5
    auth_login_window_seconds: int = 300
    google_client_id: str = ""
    google_client_secret: str = ""
    google_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_postgres_url(cls, value: object) -> object:
        """Use the bundled psycopg driver for standard hosted Postgres URLs."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        return value

    @model_validator(mode="after")
    def validate_production_auth(self) -> "Settings":
        if self.ingestion_job_heartbeat_seconds >= self.ingestion_job_lease_seconds:
            raise ValueError(
                "INGESTION_JOB_HEARTBEAT_SECONDS must be shorter than "
                "INGESTION_JOB_LEASE_SECONDS"
            )
        if self.app_env == "production":
            if not self.auth_cookie_secure:
                raise ValueError("AUTH_COOKIE_SECURE must be true in production")
            if (
                self.auth_secret == "development-only-change-me"
                or len(self.auth_secret.encode("utf-8")) < 32
            ):
                raise ValueError("AUTH_SECRET must contain at least 32 bytes in production")
        google_values = (
            self.google_client_id,
            self.google_client_secret,
            self.google_redirect_uri,
        )
        if (
            self.app_env == "production"
            and (self.google_client_id or self.google_client_secret)
            and not all(google_values)
        ):
            raise ValueError("Google auth configuration must be complete")
        if (
            self.app_env == "production"
            and all(google_values)
            and not self.google_redirect_uri.startswith("https://")
        ):
            raise ValueError("GOOGLE_REDIRECT_URI must use HTTPS in production")
        return self

    @property
    def google_auth_available(self) -> bool:
        configured = bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_redirect_uri
        )
        return configured and (
            self.app_env != "production"
            or self.google_redirect_uri.startswith("https://")
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Removed settings in an older .env must not block a rolling upgrade.
        extra="ignore",
    )


def parse_allowed_origins(raw: str) -> list[str]:
    """Split ALLOWED_ORIGINS into the list CORSMiddleware expects.

    Returns ``["*"]`` only when the operator asked for it explicitly. An empty
    or blank value yields an empty list, which blocks every cross-origin
    caller; same-origin use of the bundled PWA is unaffected either way.
    """
    value = (raw or "").strip()
    if value == "*":
        return ["*"]
    seen: list[str] = []
    for chunk in value.split(","):
        origin = chunk.strip().rstrip("/")
        if origin and origin not in seen:
            seen.append(origin)
    return seen


def origins_are_open(origins: list[str]) -> bool:
    """True when the resolved list lets any web page call this API."""
    return "*" in origins


_OPEN_CORS_MESSAGE = (
    "ALLOWED_ORIGINS is '*': any website can call this API from a visitor's "
    "browser. Set ALLOWED_ORIGINS to the front-end origins you actually serve. "
    "The bundled PWA is same-origin and does not need it."
)

_CLOSED_CORS_MESSAGE = (
    "ALLOWED_ORIGINS is empty: cross-origin browser calls are blocked. The "
    "bundled PWA still works because it is served from this origin."
)


def cors_startup_notice(origins: list[str], app_env: str) -> tuple[str, str] | None:
    """What an operator should be told about this CORS list at startup.

    Returns ``(level, message)`` where level is a ``logging`` method name, or
    ``None`` when the configuration needs no comment. A wildcard is an error in
    production and a warning elsewhere, because in production it is a live
    exposure rather than a convenience.
    """
    if origins_are_open(origins):
        if app_env.strip().lower() == "production":
            return "error", f"{_OPEN_CORS_MESSAGE} Running in production with this setting."
        return "warning", _OPEN_CORS_MESSAGE
    if not origins:
        return "info", _CLOSED_CORS_MESSAGE
    return None


# Singleton dung chung trong toan app
settings = Settings()
