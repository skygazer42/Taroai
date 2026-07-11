import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from taroai.agent_engines.models import (
    AgentEngineConnection,
    AgentEngineEvent,
    AgentEngineSession,
)
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.store import NotFoundError


class AgentEngineRegistry(BaseModel):
    def save_connection(self, connection: AgentEngineConnection) -> AgentEngineConnection:
        raise NotImplementedError

    def get_connection(self, tenant_id: str, connection_id: str) -> AgentEngineConnection:
        raise NotImplementedError

    def list_connections(self, tenant_id: str, workspace_id: str) -> list[AgentEngineConnection]:
        raise NotImplementedError

    def save_session(self, session: AgentEngineSession) -> AgentEngineSession:
        raise NotImplementedError

    def get_session(self, tenant_id: str, session_id: str) -> AgentEngineSession:
        raise NotImplementedError

    def list_sessions(self, tenant_id: str, workspace_id: str) -> list[AgentEngineSession]:
        raise NotImplementedError

    def append_event(self, event: AgentEngineEvent) -> AgentEngineEvent:
        raise NotImplementedError

    def list_events(self, tenant_id: str, session_id: str, after_sequence: int = 0) -> list[AgentEngineEvent]:
        raise NotImplementedError


class InMemoryAgentEngineRegistry(AgentEngineRegistry):
    connections: dict[str, AgentEngineConnection] = Field(default_factory=dict)
    sessions: dict[str, AgentEngineSession] = Field(default_factory=dict)
    events: dict[str, list[AgentEngineEvent]] = Field(default_factory=dict)

    def save_connection(self, connection: AgentEngineConnection) -> AgentEngineConnection:
        self.connections[connection.id] = connection.model_copy(deep=True)
        return connection

    def get_connection(self, tenant_id: str, connection_id: str) -> AgentEngineConnection:
        item = self.connections.get(connection_id)
        if item is None or item.tenant_id != tenant_id:
            raise NotFoundError(f"Agent Engine connection not found: {connection_id}")
        return item.model_copy(deep=True)

    def list_connections(self, tenant_id: str, workspace_id: str) -> list[AgentEngineConnection]:
        return sorted(
            [item.model_copy(deep=True) for item in self.connections.values() if item.tenant_id == tenant_id and item.workspace_id == workspace_id],
            key=lambda item: (item.created_at, item.id),
        )

    def save_session(self, session: AgentEngineSession) -> AgentEngineSession:
        self.sessions[session.id] = session.model_copy(deep=True)
        return session

    def get_session(self, tenant_id: str, session_id: str) -> AgentEngineSession:
        item = self.sessions.get(session_id)
        if item is None or item.tenant_id != tenant_id:
            raise NotFoundError(f"Agent Engine session not found: {session_id}")
        return item.model_copy(deep=True)

    def list_sessions(self, tenant_id: str, workspace_id: str) -> list[AgentEngineSession]:
        return sorted(
            [item.model_copy(deep=True) for item in self.sessions.values() if item.tenant_id == tenant_id and item.workspace_id == workspace_id],
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    def append_event(self, event: AgentEngineEvent) -> AgentEngineEvent:
        self.events.setdefault(event.session_id, []).append(event.model_copy(deep=True))
        return event

    def list_events(self, tenant_id: str, session_id: str, after_sequence: int = 0) -> list[AgentEngineEvent]:
        self.get_session(tenant_id, session_id)
        return [item.model_copy(deep=True) for item in self.events.get(session_id, []) if item.sequence > after_sequence]

    def next_sequence(self, tenant_id: str, session_id: str) -> int:
        self.get_session(tenant_id, session_id)
        return len(self.events.get(session_id, [])) + 1


class SqlAgentEngineRegistry(AgentEngineRegistry):
    config: DatabaseConfig

    def save_connection(self, item: AgentEngineConnection) -> AgentEngineConnection:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_engine_connections (
                    id, tenant_id, workspace_id, name, engine_type, endpoint_url,
                    secret_ref_id, status, capabilities, metadata,
                    created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name, endpoint_url = excluded.endpoint_url,
                    secret_ref_id = excluded.secret_ref_id, status = excluded.status,
                    capabilities = excluded.capabilities, metadata = excluded.metadata,
                    updated_at = excluded.updated_at
                """,
                (item.id, item.tenant_id, item.workspace_id, item.name,
                 item.engine_type.value, item.endpoint_url, item.secret_ref_id,
                 item.status, self._json(item.capabilities), self._json(item.metadata),
                 item.created_by_user_id, self._dt(item.created_at), self._dt(item.updated_at)),
            )
        return item

    def get_connection(self, tenant_id: str, connection_id: str) -> AgentEngineConnection:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_engine_connections WHERE tenant_id = ? AND id = ?",
                (tenant_id, connection_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Agent Engine connection not found: {connection_id}")
        return self._connection(row)

    def list_connections(self, tenant_id: str, workspace_id: str) -> list[AgentEngineConnection]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_engine_connections WHERE tenant_id = ? AND workspace_id = ? ORDER BY created_at, id",
                (tenant_id, workspace_id),
            ).fetchall()
        return [self._connection(row) for row in rows]

    def save_session(self, item: AgentEngineSession) -> AgentEngineSession:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_engine_sessions (
                    id, tenant_id, workspace_id, connection_id, engine_type, run_id,
                    external_session_id, status, cwd, created_by_user_id, metadata,
                    created_at, updated_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    external_session_id = excluded.external_session_id,
                    status = excluded.status, metadata = excluded.metadata,
                    updated_at = excluded.updated_at, closed_at = excluded.closed_at
                """,
                (item.id, item.tenant_id, item.workspace_id, item.connection_id,
                 item.engine_type.value, item.run_id, item.external_session_id,
                 item.status, item.cwd, item.created_by_user_id, self._json(item.metadata),
                 self._dt(item.created_at), self._dt(item.updated_at), self._dt_optional(item.closed_at)),
            )
        return item

    def get_session(self, tenant_id: str, session_id: str) -> AgentEngineSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_engine_sessions WHERE tenant_id = ? AND id = ?",
                (tenant_id, session_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Agent Engine session not found: {session_id}")
        return self._session(row)

    def list_sessions(self, tenant_id: str, workspace_id: str) -> list[AgentEngineSession]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_engine_sessions WHERE tenant_id = ? AND workspace_id = ? ORDER BY created_at DESC, id DESC",
                (tenant_id, workspace_id),
            ).fetchall()
        return [self._session(row) for row in rows]

    def append_event(self, item: AgentEngineEvent) -> AgentEngineEvent:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_engine_events (id, tenant_id, workspace_id, session_id, sequence, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item.id, item.tenant_id, item.workspace_id, item.session_id,
                 item.sequence, item.event_type, self._json(item.payload), self._dt(item.created_at)),
            )
        return item

    def list_events(self, tenant_id: str, session_id: str, after_sequence: int = 0) -> list[AgentEngineEvent]:
        self.get_session(tenant_id, session_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_engine_events WHERE tenant_id = ? AND session_id = ? AND sequence > ? ORDER BY sequence",
                (tenant_id, session_id, after_sequence),
            ).fetchall()
        return [AgentEngineEvent(
            id=row["id"], tenant_id=row["tenant_id"], workspace_id=row["workspace_id"],
            session_id=row["session_id"], sequence=int(row["sequence"]),
            event_type=row["event_type"], payload=self._loads(row["payload"]),
            created_at=self._parse(row["created_at"]),
        ) for row in rows]

    def _connection(self, row) -> AgentEngineConnection:
        return AgentEngineConnection(
            id=row["id"], tenant_id=row["tenant_id"], workspace_id=row["workspace_id"],
            name=row["name"], engine_type=row["engine_type"], endpoint_url=row["endpoint_url"],
            secret_ref_id=row["secret_ref_id"], status=row["status"],
            capabilities=self._loads(row["capabilities"]), metadata=self._loads(row["metadata"]),
            created_by_user_id=row["created_by_user_id"], created_at=self._parse(row["created_at"]),
            updated_at=self._parse(row["updated_at"]),
        )

    def _session(self, row) -> AgentEngineSession:
        return AgentEngineSession(
            id=row["id"], tenant_id=row["tenant_id"], workspace_id=row["workspace_id"],
            connection_id=row["connection_id"], engine_type=row["engine_type"], run_id=row["run_id"],
            external_session_id=row["external_session_id"], status=row["status"], cwd=row["cwd"],
            created_by_user_id=row["created_by_user_id"], metadata=self._loads(row["metadata"]),
            created_at=self._parse(row["created_at"]), updated_at=self._parse(row["updated_at"]),
            closed_at=self._parse(row["closed_at"]) if row["closed_at"] is not None else None,
        )

    def _connect(self):
        return connect_database(self.config)

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: Any):
        return value if not isinstance(value, str) else json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _dt_optional(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    def _parse(self, value: Any) -> datetime:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
