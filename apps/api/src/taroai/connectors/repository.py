import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from taroai.connectors.models import (
    ConnectorAuthMode,
    ConnectorCapability,
    ConnectorCredentialRef,
    ConnectorDefinition,
    ConnectorDefinitionCreate,
    ConnectorSyncState,
    ConnectorSyncStateUpdate,
    ConnectorStatus,
    ConnectorType,
    ConnectorUpdateRequest,
)
from taroai.connectors.service import (
    ConnectorAccessDeniedError,
    ConnectorNotFoundError,
)
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now


class SqlConnectorRegistry(BaseModel):
    config: DatabaseConfig

    def register_connector(
        self,
        create: ConnectorDefinitionCreate,
    ) -> ConnectorDefinition:
        self._assert_credential_scope(create)
        connector = ConnectorDefinition.from_create(create)
        with self._connect() as connection:
            self._ensure_tenant(connection, connector.tenant_id)
            self._ensure_workspace(
                connection,
                connector.tenant_id,
                connector.workspace_id,
            )
            connection.execute(
                """
                INSERT INTO connector_definitions (
                    id, tenant_id, workspace_id, type, display_name,
                    owner_user_id, auth_mode, credential_ref, capabilities,
                    sensitivity_level, status, metadata, sync_state, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._connector_values(connector),
            )
        return connector

    def get_connector(
        self,
        tenant_id: str,
        connector_id: str,
    ) -> ConnectorDefinition:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM connector_definitions WHERE id = ?",
                (connector_id,),
            ).fetchone()
        if row is None:
            raise ConnectorNotFoundError(f"connector not found: {connector_id}")
        connector = self._connector_from_row(row)
        if connector.tenant_id != tenant_id:
            raise ConnectorAccessDeniedError("connector is not in tenant")
        return connector

    def list_connectors(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> list[ConnectorDefinition]:
        params: tuple[Any, ...]
        if workspace_id is None:
            sql = """
                SELECT * FROM connector_definitions
                WHERE tenant_id = ?
                ORDER BY created_at, id
            """
            params = (tenant_id,)
        else:
            sql = """
                SELECT * FROM connector_definitions
                WHERE tenant_id = ? AND workspace_id = ?
                ORDER BY created_at, id
            """
            params = (tenant_id, workspace_id)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._connector_from_row(row) for row in rows]

    def update_connector(
        self,
        tenant_id: str,
        connector_id: str,
        update: ConnectorUpdateRequest,
    ) -> ConnectorDefinition:
        connector = self.get_connector(tenant_id, connector_id)
        updated = connector.apply_update(update)
        self._save_connector(updated)
        return updated

    def update_connector_sync_state(
        self,
        tenant_id: str,
        connector_id: str,
        update: ConnectorSyncStateUpdate,
    ) -> ConnectorDefinition:
        connector = self.get_connector(tenant_id, connector_id)
        updated = connector.apply_sync_state(update)
        self._save_connector(updated)
        return updated

    def update_connector_status(
        self,
        tenant_id: str,
        connector_id: str,
        status: ConnectorStatus,
    ) -> ConnectorDefinition:
        connector = self.get_connector(tenant_id, connector_id)
        updated = connector.apply_status(status)
        self._save_connector(updated)
        return updated

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

    def _assert_credential_scope(self, create: ConnectorDefinitionCreate) -> None:
        credential_ref = create.credential_ref
        if credential_ref is None:
            return
        if credential_ref.tenant_id != create.tenant_id:
            raise ConnectorAccessDeniedError("connector credential is not in tenant")
        if (
            credential_ref.workspace_id is not None
            and credential_ref.workspace_id != create.workspace_id
        ):
            raise ConnectorAccessDeniedError("connector credential is not in workspace")

    def _save_connector(self, connector: ConnectorDefinition) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE connector_definitions
                SET display_name = ?,
                    capabilities = ?,
                    sensitivity_level = ?,
                    status = ?,
                    metadata = ?,
                    sync_state = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    connector.display_name,
                    self._json([
                        capability.model_dump(mode="json")
                        for capability in connector.capabilities
                    ]),
                    connector.sensitivity_level,
                    connector.status.value,
                    self._json(connector.metadata),
                    self._sync_state_json(connector.sync_state),
                    self._dt(connector.updated_at),
                    connector.tenant_id,
                    connector.id,
                ),
            )

    def _connector_values(self, connector: ConnectorDefinition) -> tuple[Any, ...]:
        return (
            connector.id,
            connector.tenant_id,
            connector.workspace_id,
            connector.type.value,
            connector.display_name,
            connector.owner_user_id,
            connector.auth_mode.value,
            self._credential_ref_json(connector.credential_ref),
            self._json([
                capability.model_dump(mode="json")
                for capability in connector.capabilities
            ]),
            connector.sensitivity_level,
            connector.status.value,
            self._json(connector.metadata),
            self._sync_state_json(connector.sync_state),
            self._dt(connector.created_at),
            self._dt(connector.updated_at),
        )

    def _connector_from_row(self, row) -> ConnectorDefinition:
        credential_ref_payload = (
            self._loads(row["credential_ref"])
            if row["credential_ref"] is not None
            else None
        )
        return ConnectorDefinition(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            type=ConnectorType(row["type"]),
            display_name=row["display_name"],
            owner_user_id=row["owner_user_id"],
            auth_mode=ConnectorAuthMode(row["auth_mode"]),
            credential_ref=(
                ConnectorCredentialRef.model_validate(credential_ref_payload)
                if credential_ref_payload is not None
                else None
            ),
            capabilities=[
                ConnectorCapability.model_validate(capability)
                for capability in self._loads(row["capabilities"])
            ],
            sensitivity_level=int(row["sensitivity_level"]),
            status=ConnectorStatus(row["status"]),
            metadata=self._loads(row["metadata"]),
            sync_state=(
                ConnectorSyncState.model_validate(self._loads(row["sync_state"]))
                if "sync_state" in row.keys() and row["sync_state"] is not None
                else None
            ),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _credential_ref_json(self, value: ConnectorCredentialRef | None) -> str | None:
        if value is None:
            return None
        return self._json(value.model_dump(mode="json"))

    def _sync_state_json(self, value: ConnectorSyncState | None) -> str | None:
        if value is None:
            return None
        return self._json(value.model_dump(mode="json"))

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: str) -> Any:
        return json.loads(value)

    def _dt(self, value: datetime) -> str:
        resolved = value
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc).isoformat()

    def _parse_dt(self, value) -> datetime:
        if isinstance(value, datetime):
            resolved = value
        else:
            resolved = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if resolved.tzinfo is None:
            return resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc)
