from datetime import datetime

from pydantic import BaseModel, Field

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import new_id


class AuthSession(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


class AuthSessionStore(BaseModel):
    def create_session(
        self,
        tenant_id: str,
        user_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> AuthSession:
        raise NotImplementedError

    def get_session(self, tenant_id: str, session_id: str) -> AuthSession | None:
        raise NotImplementedError

    def revoke_session(self, tenant_id: str, session_id: str, revoked_at: datetime) -> bool:
        raise NotImplementedError

    def revoke_user_sessions(
        self,
        tenant_id: str,
        user_id: str,
        revoked_at: datetime,
    ) -> int:
        raise NotImplementedError


class InMemoryAuthSessionStore(AuthSessionStore):
    sessions: dict[str, AuthSession] = Field(default_factory=dict)

    def create_session(
        self,
        tenant_id: str,
        user_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> AuthSession:
        session = AuthSession(
            id=new_id("session"),
            tenant_id=tenant_id,
            user_id=user_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self.sessions[session.id] = session
        return session

    def get_session(self, tenant_id: str, session_id: str) -> AuthSession | None:
        session = self.sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            return None
        return session

    def revoke_session(self, tenant_id: str, session_id: str, revoked_at: datetime) -> bool:
        session = self.get_session(tenant_id, session_id)
        if session is None:
            return False
        self.sessions[session_id] = session.model_copy(update={"revoked_at": revoked_at})
        return True

    def revoke_user_sessions(
        self,
        tenant_id: str,
        user_id: str,
        revoked_at: datetime,
    ) -> int:
        session_ids = [
            session_id
            for session_id, session in self.sessions.items()
            if session.tenant_id == tenant_id
            and session.user_id == user_id
            and session.revoked_at is None
        ]
        for session_id in session_ids:
            self.sessions[session_id] = self.sessions[session_id].model_copy(
                update={"revoked_at": revoked_at}
            )
        return len(session_ids)


class SqlAuthSessionStore(AuthSessionStore):
    config: DatabaseConfig

    def create_session(
        self,
        tenant_id: str,
        user_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> AuthSession:
        session = AuthSession(
            id=new_id("session"),
            tenant_id=tenant_id,
            user_id=user_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions (
                    id, tenant_id, user_id, issued_at, expires_at, revoked_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.tenant_id,
                    session.user_id,
                    self._dt(session.issued_at),
                    self._dt(session.expires_at),
                    None,
                ),
            )
        return session

    def get_session(self, tenant_id: str, session_id: str) -> AuthSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM auth_sessions WHERE tenant_id = ? AND id = ?",
                (tenant_id, session_id),
            ).fetchone()
        if row is None:
            return None
        return self._session_from_row(row)

    def revoke_session(self, tenant_id: str, session_id: str, revoked_at: datetime) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE tenant_id = ? AND id = ? AND revoked_at IS NULL
                """,
                (self._dt(revoked_at), tenant_id, session_id),
            )
        return cursor.rowcount > 0

    def revoke_user_sessions(
        self,
        tenant_id: str,
        user_id: str,
        revoked_at: datetime,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE tenant_id = ? AND user_id = ? AND revoked_at IS NULL
                """,
                (self._dt(revoked_at), tenant_id, user_id),
            )
        return cursor.rowcount

    def _connect(self):
        return connect_database(self.config)

    def _session_from_row(self, row) -> AuthSession:
        return AuthSession(
            id=row["id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            issued_at=self._parse_dt(row["issued_at"]),
            expires_at=self._parse_dt(row["expires_at"]),
            revoked_at=self._parse_dt(row["revoked_at"]) if row["revoked_at"] else None,
        )

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)
