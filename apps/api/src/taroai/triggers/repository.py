import json
from datetime import datetime, timezone
from typing import Any


from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.store import NotFoundError, TenantAccessError
from taroai.triggers.models import (
    TriggerAgentHandoffConfig,
    TriggerConnectorEventConfig,
    TriggerDefinition,
    TriggerDefinitionCreate,
    TriggerScheduleConfig,
    TriggerStatus,
    TriggerType,
)
from taroai.triggers.service import TriggerStore


class SqlTriggerStore(TriggerStore):
    config: DatabaseConfig

    def create(self, payload: TriggerDefinitionCreate) -> TriggerDefinition:
        trigger = TriggerDefinition(**payload.model_dump())
        with self._connect() as connection:
            self._ensure_tenant(connection, trigger.tenant_id)
            self._ensure_workspace(connection, trigger.tenant_id, trigger.workspace_id)
            connection.execute(
                """
                INSERT INTO trigger_definitions (
                    id, tenant_id, workspace_id, agent_id, created_by_user_id,
                    service_account_id, type, name, status, input_template,
                    policy_profile, budget_profile, schedule, connector_event,
                    agent_handoff, next_run_at,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._trigger_values(trigger),
            )
        return trigger

    def get(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM trigger_definitions WHERE tenant_id = ? AND id = ?",
                (tenant_id, trigger_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Trigger not found: {trigger_id}")
        trigger = self._trigger_from_row(row)
        if trigger.tenant_id != tenant_id:
            raise TenantAccessError(f"Trigger {trigger_id} is not in tenant {tenant_id}")
        return trigger

    def list_by_tenant(self, tenant_id: str) -> list[TriggerDefinition]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM trigger_definitions
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._trigger_from_row(row) for row in rows]

    def list_all(self) -> list[TriggerDefinition]:
        with self._connect() as connection:
            tenants = connection.execute(
                "SELECT id FROM tenants ORDER BY id"
            ).fetchall()
        return [
            trigger
            for tenant in tenants
            for trigger in self.list_by_tenant(str(tenant["id"]))
        ]

    def update_status(
        self,
        tenant_id: str,
        trigger_id: str,
        status: TriggerStatus,
    ) -> TriggerDefinition:
        trigger = self.get(tenant_id, trigger_id)
        updated = trigger.model_copy(
            update={
                "status": status,
                "updated_at": utc_now(),
            }
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trigger_definitions
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    updated.status.value,
                    self._dt(updated.updated_at),
                    tenant_id,
                    trigger_id,
                ),
            )
        return updated

    def update_next_run_at(
        self,
        tenant_id: str,
        trigger_id: str,
        next_run_at: datetime | None,
    ) -> TriggerDefinition:
        trigger = self.get(tenant_id, trigger_id)
        updated = trigger.model_copy(
            update={
                "next_run_at": next_run_at,
                "updated_at": utc_now(),
            }
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trigger_definitions
                SET next_run_at = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    self._dt_or_none(updated.next_run_at),
                    self._dt(updated.updated_at),
                    tenant_id,
                    trigger_id,
                ),
            )
        return updated

    def delete(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        trigger = self.get(tenant_id, trigger_id)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM trigger_definitions WHERE tenant_id = ? AND id = ?",
                (tenant_id, trigger_id),
            )
        return trigger

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _ensure_workspace(self, connection, tenant_id: str, workspace_id: str) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (workspace_id, tenant_id, workspace_id, self._dt(utc_now())),
        )

    def _trigger_values(self, trigger: TriggerDefinition) -> tuple[Any, ...]:
        return (
            trigger.id,
            trigger.tenant_id,
            trigger.workspace_id,
            trigger.agent_id,
            trigger.created_by_user_id,
            trigger.service_account_id,
            trigger.type.value,
            trigger.name,
            trigger.status.value,
            self._json(trigger.input_template),
            trigger.policy_profile,
            trigger.budget_profile,
            self._schedule_json(trigger.schedule),
            self._connector_event_json(trigger.connector_event),
            self._agent_handoff_json(trigger.agent_handoff),
            self._dt_or_none(trigger.next_run_at),
            self._dt(trigger.created_at),
            self._dt(trigger.updated_at),
        )

    def _trigger_from_row(self, row) -> TriggerDefinition:
        schedule_payload = self._loads(row["schedule"]) if row["schedule"] is not None else None
        connector_event_payload = (
            self._loads(row["connector_event"])
            if "connector_event" in row.keys() and row["connector_event"] is not None
            else None
        )
        agent_handoff_payload = (
            self._loads(row["agent_handoff"])
            if "agent_handoff" in row.keys() and row["agent_handoff"] is not None
            else None
        )
        return TriggerDefinition(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            agent_id=row["agent_id"],
            created_by_user_id=row["created_by_user_id"],
            service_account_id=row["service_account_id"],
            type=TriggerType(row["type"]),
            name=row["name"],
            status=TriggerStatus(row["status"]),
            input_template=self._loads(row["input_template"]),
            policy_profile=row["policy_profile"],
            budget_profile=row["budget_profile"],
            schedule=(
                TriggerScheduleConfig.model_validate(schedule_payload)
                if schedule_payload is not None
                else None
            ),
            connector_event=(
                TriggerConnectorEventConfig.model_validate(connector_event_payload)
                if connector_event_payload is not None
                else None
            ),
            agent_handoff=(
                TriggerAgentHandoffConfig.model_validate(agent_handoff_payload)
                if agent_handoff_payload is not None
                else None
            ),
            next_run_at=self._parse_dt_or_none(row["next_run_at"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _schedule_json(self, value: TriggerScheduleConfig | None) -> str | None:
        if value is None:
            return None
        return self._json(value.model_dump(mode="json"))

    def _connector_event_json(
        self,
        value: TriggerConnectorEventConfig | None,
    ) -> str | None:
        if value is None:
            return None
        return self._json(value.model_dump(mode="json"))

    def _agent_handoff_json(
        self,
        value: TriggerAgentHandoffConfig | None,
    ) -> str | None:
        if value is None:
            return None
        return self._json(value.model_dump(mode="json"))

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    def _dt_or_none(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return self._dt(value)

    def _dt(self, value: datetime) -> str:
        resolved = value
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc).isoformat()

    def _parse_dt_or_none(self, value) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)

    def _parse_dt(self, value) -> datetime:
        if isinstance(value, datetime):
            resolved = value
        else:
            resolved = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if resolved.tzinfo is None:
            return resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc)
