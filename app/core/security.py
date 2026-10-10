"""Shared lightweight protection for administrative endpoints.

Both guards fail closed. An empty ``ADMIN_API_KEY`` used to switch
``require_admin`` off entirely, and because setup copies ``.env.example`` --
where the key is blank -- every default install served document upload and
deletion to anyone who could reach the app. A forgotten setting must not be the
thing that opens the door.
"""
import hmac

from fastapi import Header, HTTPException

from app.core.config import settings


def _key_matches(supplied: str | None) -> bool:
    # Constant-time, so the comparison does not leak how much of a guess was
    # right. Only called once a key is known to be configured.
    return hmac.compare_digest(
        (supplied or "").encode("utf-8"),
        settings.admin_api_key.encode("utf-8"),
    )


def require_configured_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Fail closed for endpoints that return raw internal retrieval records.

    Answers 404 when no key is configured, so an unconfigured deployment does
    not advertise that an operator surface exists behind the door.
    """
    if not settings.admin_api_key:
        raise HTTPException(status_code=404, detail="Not found.")
    if not _key_matches(x_admin_key):
        raise HTTPException(status_code=401, detail="Admin API key is invalid.")


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Require X-Admin-Key for administrative writes, and refuse when unset.

    Answers 503 rather than 404 when no key is configured. These are operator
    actions -- upload a source document, delete one, create a hotel -- whose
    routes are already listed in the OpenAPI schema, so hiding them buys
    nothing, while an operator whose upload silently "does not exist" loses an
    afternoon. The message names the setting to fix.
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "Admin API chưa được bật: hãy đặt ADMIN_API_KEY trong .env rồi "
                "khởi động lại. SETUP_AND_RUN.bat tự sinh khóa khi tạo .env mới."
            ),
        )
    if not _key_matches(x_admin_key):
        raise HTTPException(status_code=401, detail="Admin API key không hợp lệ.")
