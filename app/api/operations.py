"""Admin-only aggregate operating signals without raw guest content."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_configured_admin
from app.services.chat_metrics import summarize_chat_telemetry
from app.services.message_feedback import summarise


router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.get(
    "/summary",
    dependencies=[Depends(require_configured_admin)],
)
def operations_summary(db: Session = Depends(get_db)) -> dict[str, object]:
    return {
        "telemetry": summarize_chat_telemetry(),
        "feedback": {
            "all": summarise(db),
            "travel": summarise(db, context_type="TRAVEL"),
            "hotel": summarise(db, context_type="HOTEL"),
        },
    }
