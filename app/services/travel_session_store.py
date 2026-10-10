"""Database-backed chat sessions shared across restarts and API workers."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.core.database import SessionLocal
from app.models.knowledge import ChatMessage, ChatSession


MAX_HISTORY_TURNS = 4
SESSION_TTL_SECONDS = 2 * 60 * 60


class TravelChatSession:
    def __init__(self, session_id: str, scope_key: str, history: list[dict]) -> None:
        self.session_id = session_id
        self.scope_key = scope_key
        self.history = history

    def add_turn(self, role: str, content: str) -> None:
        db = SessionLocal()
        try:
            session = db.get(ChatSession, self.session_id)
            if session is None:
                session = ChatSession(id=self.session_id, scope_key=self.scope_key)
                db.add(session)
            session.last_active = datetime.utcnow()
            db.add(ChatMessage(session_id=self.session_id, role=role, content=content))
            db.flush()
            keep_ids = [
                row[0]
                for row in db.query(ChatMessage.id)
                .filter(ChatMessage.session_id == self.session_id)
                .order_by(ChatMessage.id.desc())
                .limit(MAX_HISTORY_TURNS * 2)
                .all()
            ]
            if keep_ids:
                db.query(ChatMessage).filter(
                    ChatMessage.session_id == self.session_id,
                    ChatMessage.id.notin_(keep_ids),
                ).delete(synchronize_session=False)
            db.commit()
            self.history.append({"role": role, "content": content})
            self.history = self.history[-(MAX_HISTORY_TURNS * 2):]
        finally:
            db.close()


class TravelSessionStore:
    def _cleanup_expired(self) -> None:
        cutoff = datetime.utcnow() - timedelta(seconds=SESSION_TTL_SECONDS)
        db = SessionLocal()
        try:
            expired = db.query(ChatSession.id).filter(
                ChatSession.last_active < cutoff
            ).all()
            expired_ids = [row[0] for row in expired]
            if expired_ids:
                db.query(ChatMessage).filter(
                    ChatMessage.session_id.in_(expired_ids)
                ).delete(synchronize_session=False)
                db.query(ChatSession).filter(
                    ChatSession.id.in_(expired_ids)
                ).delete(synchronize_session=False)
                db.commit()
        finally:
            db.close()

    def get_or_create(self, scope_key: str, session_id: str | None) -> TravelChatSession:
        self._cleanup_expired()
        db = SessionLocal()
        try:
            stored = db.get(ChatSession, session_id) if session_id else None
            if stored is None or stored.scope_key != scope_key:
                stored = ChatSession(id=str(uuid.uuid4()), scope_key=scope_key)
                db.add(stored)
                db.commit()
                db.refresh(stored)
            else:
                stored.last_active = datetime.utcnow()
                db.commit()
            messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == stored.id)
                .order_by(ChatMessage.id.desc())
                .limit(MAX_HISTORY_TURNS * 2)
                .all()
            )
            history = [
                {"role": message.role, "content": message.content}
                for message in reversed(messages)
            ]
            return TravelChatSession(stored.id, stored.scope_key, history)
        finally:
            db.close()


travel_session_store = TravelSessionStore()
