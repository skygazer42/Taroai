from typing import Any

from taroai.agent_engines.adapter import RemoteAgentEngineAdapter
from taroai.agent_engines.models import (
    AgentEngineConnection,
    AgentEngineConnectionCreate,
    AgentEngineConnectionPatch,
    AgentEngineEvent,
    AgentEngineSession,
    AgentEngineSessionCreate,
)
from taroai.agent_engines.repository import AgentEngineRegistry
from taroai.domain import utc_now


class AgentEngineService:
    def __init__(self, registry: AgentEngineRegistry, secret_service: Any, adapter: Any | None = None, store: Any | None = None):
        self.registry = registry
        self.secret_service = secret_service
        self.adapter = adapter or RemoteAgentEngineAdapter()
        self.store = store

    def create_connection(self, tenant_id: str, user_id: str, payload: AgentEngineConnectionCreate) -> AgentEngineConnection:
        if payload.engine_type.value != "native" and not payload.endpoint_url:
            raise ValueError("Remote Agent Engine connections require endpoint_url")
        return self.registry.save_connection(AgentEngineConnection(tenant_id=tenant_id, created_by_user_id=user_id, **payload.model_dump()))

    def update_connection(self, tenant_id: str, connection_id: str, payload: AgentEngineConnectionPatch) -> AgentEngineConnection:
        connection = self.registry.get_connection(tenant_id, connection_id)
        updated = connection.model_copy(update={**payload.model_dump(exclude_none=True), "updated_at": utc_now()})
        return self.registry.save_connection(updated)

    def start_session(self, tenant_id: str, user_id: str, payload: AgentEngineSessionCreate) -> AgentEngineSession:
        connection = self.registry.get_connection(tenant_id, payload.connection_id)
        if connection.workspace_id != payload.workspace_id or connection.status != "active":
            raise ValueError("Agent Engine connection is not active in this workspace")
        session = AgentEngineSession(
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            connection_id=connection.id,
            engine_type=connection.engine_type,
            run_id=payload.run_id,
            cwd=payload.cwd,
            created_by_user_id=user_id,
            metadata=payload.metadata,
        )
        self.registry.save_session(session)
        if connection.engine_type.value == "native":
            session = session.model_copy(update={"external_session_id": session.id, "status": "running", "updated_at": utc_now()})
            self.registry.save_session(session)
            self._event(session, "engine.session.started", {"task": payload.task, "native": True})
            self._sync_run(session)
            return session
        try:
            result = self.adapter.create_session(connection, {"session_id": session.id, "workspace_id": session.workspace_id, "run_id": session.run_id, "task": payload.task, "cwd": session.cwd, "metadata": session.metadata}, self._token(connection, session))
            session = session.model_copy(update={"external_session_id": str(result.get("session_id") or result.get("id") or session.id), "status": str(result.get("status") or "running"), "updated_at": utc_now()})
            self.registry.save_session(session)
            self._ingest(session, result)
            self._sync_run(session)
            return session
        except Exception as error:
            session = session.model_copy(update={"status": "failed", "updated_at": utc_now(), "metadata": {**session.metadata, "start_error": str(error)}})
            self.registry.save_session(session)
            self._event(session, "engine.session.failed", {"error": str(error)})
            raise

    def operation(self, tenant_id: str, session_id: str, operation: str, payload: dict[str, Any] | None = None, method: str = "POST") -> AgentEngineSession:
        session = self.registry.get_session(tenant_id, session_id)
        connection = self.registry.get_connection(tenant_id, session.connection_id)
        if connection.engine_type.value == "native":
            self._event(session, f"engine.{operation}.accepted", payload or {})
            statuses = {"cancel": "cancelled", "resume": "running", "close": "closed"}
            if operation in statuses:
                updates = {"status": statuses[operation], "updated_at": utc_now()}
                if operation == "close":
                    updates["closed_at"] = utc_now()
                session = session.model_copy(update=updates)
                self.registry.save_session(session)
            return session
        result = self.adapter.operation(connection, session, operation, payload, self._token(connection, session), method)
        status = str(result.get("status") or session.status)
        updates = {"status": status, "updated_at": utc_now()}
        if status == "closed":
            updates["closed_at"] = utc_now()
        session = session.model_copy(update=updates)
        self.registry.save_session(session)
        self._ingest(session, result)
        self._sync_run(session)
        return session

    def refresh_events(self, tenant_id: str, session_id: str) -> list[AgentEngineEvent]:
        session = self.registry.get_session(tenant_id, session_id)
        connection = self.registry.get_connection(tenant_id, session.connection_id)
        if connection.engine_type.value != "native":
            cursor = int(session.metadata.get("engine_event_cursor") or 0)
            result = self.adapter.operation(connection, session, f"events?after_sequence={cursor}", None, self._token(connection, session), "GET")
            self._ingest(session, result)
            received = result.get("events") if isinstance(result.get("events"), list) else []
            next_cursor = int(result.get("next_sequence") or (cursor + len(received)))
            if next_cursor != cursor:
                session = session.model_copy(update={"metadata": {**session.metadata, "engine_event_cursor": next_cursor}, "updated_at": utc_now()})
                self.registry.save_session(session)
        self._sync_run(session)
        return self.registry.list_events(tenant_id, session_id)

    def _token(self, connection: AgentEngineConnection, session: AgentEngineSession) -> str | None:
        if not connection.secret_ref_id:
            return None
        lease = self.secret_service.create_lease(
            tenant_id=connection.tenant_id,
            workspace_id=connection.workspace_id,
            secret_id=connection.secret_ref_id,
            tool_name="agent_engine",
            actions=["read"],
            ttl_seconds=60,
            run_id=session.run_id,
            session_id=session.id,
        )
        return self.secret_service.resolve_lease_value(
            tenant_id=connection.tenant_id,
            lease_token=lease.lease_token,
            workspace_id=connection.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            tool_name="agent_engine",
            action="read",
            require_bound_context=True,
        )

    def _ingest(self, session: AgentEngineSession, result: dict[str, Any]) -> None:
        events = result.get("events") or []
        if not isinstance(events, list):
            return
        for item in events:
            if not isinstance(item, dict):
                continue
            self._event(session, str(item.get("type") or item.get("event_type") or "engine.event"), dict(item.get("payload") or item))

    def _event(self, session: AgentEngineSession, event_type: str, payload: dict[str, Any]) -> AgentEngineEvent:
        sequence = len(self.registry.list_events(session.tenant_id, session.id)) + 1
        return self.registry.append_event(AgentEngineEvent(tenant_id=session.tenant_id, workspace_id=session.workspace_id, session_id=session.id, sequence=sequence, event_type=event_type, payload=payload))

    def _sync_run(self, session: AgentEngineSession) -> None:
        if self.store is None or session.run_id is None:
            return
        run = self.store.get_run(session.tenant_id, session.run_id)
        synced = int(session.metadata.get("run_event_sync_sequence") or 0)
        events = self.registry.list_events(session.tenant_id, session.id, synced)
        for event in events:
            self.store.append_run_event(run, event.event_type, {"engine_session_id": session.id, "engine_type": session.engine_type.value, **event.payload})
            synced = max(synced, event.sequence)
        status_map = {
            "running": "running", "waiting_approval": "awaiting_approval",
            "completed": "succeeded", "failed": "failed",
            "cancelled": "cancelled",
        }
        target = status_map.get(session.status)
        if target:
            from taroai.domain import RunStatus
            self.store.update_run_status(session.tenant_id, session.run_id, RunStatus(target), emit_status_event=False)
        if synced != int(session.metadata.get("run_event_sync_sequence") or 0):
            session = session.model_copy(update={"metadata": {**session.metadata, "run_event_sync_sequence": synced}, "updated_at": utc_now()})
            self.registry.save_session(session)
