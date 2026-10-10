"""Single authorization boundary for unified conversations."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.unified import UnifiedConversation
from app.services.auth import AuthContext, get_current_app_session


def resolve_authorized_conversation(
    db: Session,
    conversation_id: str,
    context: AuthContext,
) -> UnifiedConversation:
    """Resolve a conversation owned by the current account or guest session.

    Ownerless rows from older schema versions are intentionally inaccessible.
    Returning 404 for both absent and unauthorized rows limits enumeration.
    """

    filters = [UnifiedConversation.id == conversation_id]
    if context.user_id:
        filters.append(UnifiedConversation.owner_user_id == context.user_id)
    else:
        filters.extend(
            [
                UnifiedConversation.owner_user_id.is_(None),
                UnifiedConversation.owner_session_id == context.session.id,
            ]
        )
    conversation = db.scalar(select(UnifiedConversation).where(*filters))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
    return conversation


def get_authorized_conversation(
    conversation_id: str,
    context: AuthContext = Depends(get_current_app_session),
    db: Session = Depends(get_db),
) -> UnifiedConversation:
    return resolve_authorized_conversation(db, conversation_id, context)
