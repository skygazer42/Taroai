import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from taroai.browser_profiles.models import BrowserProfile, BrowserProfileSession
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.store import NotFoundError


class BrowserProfileRegistry(BaseModel):
    def create_profile(self, profile: BrowserProfile) -> BrowserProfile:
        raise NotImplementedError


class InMemoryBrowserProfileRegistry(BrowserProfileRegistry):
    profiles: dict[str, BrowserProfile] = Field(default_factory=dict)
    sessions: dict[str, BrowserProfileSession] = Field(default_factory=dict)

    def create_profile(self, profile: BrowserProfile) -> BrowserProfile:
        if profile.is_default:
            self._clear_default(profile.tenant_id, profile.workspace_id)
        self.profiles[profile.id] = profile.model_copy(deep=True)
        return profile.model_copy(deep=True)

    def get_profile(self, tenant_id: str, profile_id: str) -> BrowserProfile:
        profile = self.profiles.get(profile_id)
        if profile is None or profile.tenant_id != tenant_id:
            raise NotFoundError(f"Browser profile not found: {profile_id}")
        return profile.model_copy(deep=True)

    def list_profiles(self, tenant_id: str, workspace_id: str) -> list[BrowserProfile]:
        return sorted(
            [
                item.model_copy(deep=True)
                for item in self.profiles.values()
                if item.tenant_id == tenant_id and item.workspace_id == workspace_id
            ],
            key=lambda item: (not item.is_default, item.name.casefold(), item.id),
        )

    def update_profile(self, tenant_id: str, profile_id: str, **changes) -> BrowserProfile:
        profile = self.get_profile(tenant_id, profile_id)
        if changes.get("is_default"):
            self._clear_default(tenant_id, profile.workspace_id)
        updated = profile.model_copy(update={**changes, "updated_at": utc_now()})
        self.profiles[profile_id] = updated
        return updated.model_copy(deep=True)

    def save_session(self, session: BrowserProfileSession) -> BrowserProfileSession:
        self.sessions[session.session_id] = session.model_copy(deep=True)
        return session.model_copy(deep=True)

    def get_session(self, tenant_id: str, session_id: str) -> BrowserProfileSession:
        session = self.sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            raise NotFoundError(f"Browser profile session not found: {session_id}")
        return session.model_copy(deep=True)

    def list_sessions(self, tenant_id: str, workspace_id: str) -> list[BrowserProfileSession]:
        return sorted(
            [
                item.model_copy(deep=True)
                for item in self.sessions.values()
                if item.tenant_id == tenant_id and item.workspace_id == workspace_id
            ],
            key=lambda item: (item.started_at, item.session_id),
            reverse=True,
        )

    def _clear_default(self, tenant_id: str, workspace_id: str) -> None:
        self.profiles = {
            key: (
                value.model_copy(update={"is_default": False, "updated_at": utc_now()})
                if value.tenant_id == tenant_id and value.workspace_id == workspace_id and value.is_default
                else value
            )
            for key, value in self.profiles.items()
        }


class SqlBrowserProfileRegistry(BrowserProfileRegistry):
    config: DatabaseConfig

    def create_profile(self, profile: BrowserProfile) -> BrowserProfile:
        with self._connect() as connection:
            if profile.is_default:
                connection.execute(
                    "UPDATE browser_profiles SET is_default = FALSE WHERE tenant_id = ? AND workspace_id = ?",
                    (profile.tenant_id, profile.workspace_id),
                )
            connection.execute(
                """
                INSERT INTO browser_profiles (
                    id, tenant_id, workspace_id, name, description, status,
                    secret_ref_id, secret_backend, secret_external_name,
                    allowed_domains, is_default, revision,
                    created_by_user_id, last_used_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.id, profile.tenant_id, profile.workspace_id, profile.name,
                    profile.description, profile.status, profile.secret_ref_id,
                    profile.secret_backend, profile.secret_external_name,
                    self._json(profile.allowed_domains), profile.is_default,
                    profile.revision, profile.created_by_user_id,
                    self._dt_or_none(profile.last_used_at), self._dt(profile.created_at),
                    self._dt(profile.updated_at),
                ),
            )
        return profile

    def get_profile(self, tenant_id: str, profile_id: str) -> BrowserProfile:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM browser_profiles WHERE tenant_id = ? AND id = ?",
                (tenant_id, profile_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Browser profile not found: {profile_id}")
        return self._profile(row)

    def list_profiles(self, tenant_id: str, workspace_id: str) -> list[BrowserProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM browser_profiles
                WHERE tenant_id = ? AND workspace_id = ?
                ORDER BY is_default DESC, name, id
                """,
                (tenant_id, workspace_id),
            ).fetchall()
        return [self._profile(row) for row in rows]

    def update_profile(self, tenant_id: str, profile_id: str, **changes) -> BrowserProfile:
        profile = self.get_profile(tenant_id, profile_id)
        values = profile.model_dump()
        values.update(changes)
        values["updated_at"] = utc_now()
        if values.get("is_default"):
            with self._connect() as connection:
                connection.execute(
                    "UPDATE browser_profiles SET is_default = FALSE WHERE tenant_id = ? AND workspace_id = ?",
                    (tenant_id, profile.workspace_id),
                )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE browser_profiles SET
                    name = ?, description = ?, status = ?, secret_ref_id = ?,
                    secret_backend = ?, secret_external_name = ?,
                    allowed_domains = ?, is_default = ?, revision = ?,
                    last_used_at = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    values["name"], values["description"], values["status"],
                    values["secret_ref_id"], values["secret_backend"],
                    values["secret_external_name"], self._json(values["allowed_domains"]),
                    values["is_default"], values["revision"],
                    self._dt_or_none(values["last_used_at"]), self._dt(values["updated_at"]),
                    tenant_id, profile_id,
                ),
            )
        return BrowserProfile.model_validate(values)

    def save_session(self, session: BrowserProfileSession) -> BrowserProfileSession:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO browser_profile_sessions (
                    session_id, tenant_id, workspace_id, profile_id, run_id,
                    status, current_url, created_by_user_id, started_at,
                    last_seen_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status = excluded.status,
                    current_url = excluded.current_url,
                    last_seen_at = excluded.last_seen_at,
                    closed_at = excluded.closed_at
                """,
                (
                    session.session_id, session.tenant_id, session.workspace_id,
                    session.profile_id, session.run_id, session.status,
                    session.current_url, session.created_by_user_id,
                    self._dt(session.started_at), self._dt(session.last_seen_at),
                    self._dt_or_none(session.closed_at),
                ),
            )
        return session

    def get_session(self, tenant_id: str, session_id: str) -> BrowserProfileSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM browser_profile_sessions WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Browser profile session not found: {session_id}")
        return self._session(row)

    def list_sessions(self, tenant_id: str, workspace_id: str) -> list[BrowserProfileSession]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM browser_profile_sessions
                WHERE tenant_id = ? AND workspace_id = ?
                ORDER BY started_at DESC, session_id DESC
                """,
                (tenant_id, workspace_id),
            ).fetchall()
        return [self._session(row) for row in rows]

    def _profile(self, row) -> BrowserProfile:
        return BrowserProfile(
            id=row["id"], tenant_id=row["tenant_id"], workspace_id=row["workspace_id"],
            name=row["name"], description=row["description"], status=row["status"],
            secret_ref_id=row["secret_ref_id"], secret_backend=row["secret_backend"],
            secret_external_name=row["secret_external_name"],
            allowed_domains=self._loads(row["allowed_domains"]),
            is_default=bool(row["is_default"]), revision=int(row["revision"]),
            created_by_user_id=row["created_by_user_id"],
            last_used_at=self._parse_or_none(row["last_used_at"]),
            created_at=self._parse(row["created_at"]), updated_at=self._parse(row["updated_at"]),
        )

    def _session(self, row) -> BrowserProfileSession:
        return BrowserProfileSession(
            session_id=row["session_id"], tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"], profile_id=row["profile_id"],
            run_id=row["run_id"], status=row["status"], current_url=row["current_url"],
            created_by_user_id=row["created_by_user_id"],
            started_at=self._parse(row["started_at"]),
            last_seen_at=self._parse(row["last_seen_at"]),
            closed_at=self._parse_or_none(row["closed_at"]),
        )

    def _connect(self):
        return connect_database(self.config)

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: Any):
        return value if not isinstance(value, str) else json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _dt_or_none(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    def _parse(self, value: Any) -> datetime:
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    def _parse_or_none(self, value: Any) -> datetime | None:
        return self._parse(value) if value is not None else None
