"""
Session Manager
In-memory session store for active conversations.
Replace with Redis for multi-instance / production deployments.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.models import SessionMemory

logger = logging.getLogger("trango_agent.sessions")


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionMemory] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str | None = None) -> SessionMemory:
        async with self._lock:
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                session.last_active = datetime.utcnow()
                return session
            session = SessionMemory()
            if session_id:
                session.session_id = session_id
            self._sessions[session.session_id] = session
            logger.info(f"New session: {session.session_id[:8]}")
            return session

    async def get(self, session_id: str) -> SessionMemory | None:
        return self._sessions.get(session_id)

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)
            logger.info(f"Session closed: {session_id[:8]}")

    async def cleanup_expired(self) -> int:
        """Remove sessions idle longer than SESSION_TIMEOUT_SECONDS. Returns count removed."""
        cutoff = datetime.utcnow() - timedelta(seconds=settings.SESSION_TIMEOUT_SECONDS)
        async with self._lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if s.last_active < cutoff
            ]
            for sid in expired:
                del self._sessions[sid]
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired sessions")
        return len(expired)

    def active_count(self) -> int:
        return len(self._sessions)


# Singleton
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
