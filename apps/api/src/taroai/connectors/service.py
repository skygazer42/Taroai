from pydantic import BaseModel, Field

from taroai.domain import utc_now

from taroai.connectors.models import (
    ConnectorCredentialRef,
    ConnectorDefinition,
    ConnectorDefinitionCreate,
    ConnectorSyncStateUpdate,
    ConnectorStatus,
    ConnectorUpdateRequest,
)


class ConnectorAccessDeniedError(PermissionError):
    pass


class ConnectorNotFoundError(LookupError):
    pass


class InMemoryConnectorRegistry(BaseModel):
    connectors: dict[str, ConnectorDefinition] = Field(default_factory=dict)

    def register_connector(
        self,
        create: ConnectorDefinitionCreate,
    ) -> ConnectorDefinition:
        self._assert_credential_scope(create)
        connector = ConnectorDefinition.from_create(create)
        self.connectors[connector.id] = connector
        return connector.model_copy(deep=True)

    def get_connector(
        self,
        tenant_id: str,
        connector_id: str,
    ) -> ConnectorDefinition:
        connector = self.connectors.get(connector_id)
        if connector is None:
            raise ConnectorNotFoundError(f"connector not found: {connector_id}")
        if connector.tenant_id != tenant_id:
            raise ConnectorAccessDeniedError("connector is not in tenant")
        return connector.model_copy(deep=True)

    def list_connectors(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> list[ConnectorDefinition]:
        connectors = [
            connector
            for connector in self.connectors.values()
            if connector.tenant_id == tenant_id
            and (workspace_id is None or connector.workspace_id == workspace_id)
        ]
        return [
            connector.model_copy(deep=True)
            for connector in sorted(connectors, key=lambda item: (item.created_at, item.id))
        ]

    def update_connector(
        self,
        tenant_id: str,
        connector_id: str,
        update: ConnectorUpdateRequest,
    ) -> ConnectorDefinition:
        connector = self.get_connector(tenant_id, connector_id)
        updated = connector.apply_update(update)
        self.connectors[connector_id] = updated
        return updated.model_copy(deep=True)

    def update_connector_status(
        self,
        tenant_id: str,
        connector_id: str,
        status: ConnectorStatus,
    ) -> ConnectorDefinition:
        connector = self.get_connector(tenant_id, connector_id)
        updated = connector.apply_status(status)
        self.connectors[connector_id] = updated
        return updated.model_copy(deep=True)

    def update_connector_credential(
        self,
        tenant_id: str,
        connector_id: str,
        credential_ref: ConnectorCredentialRef,
    ) -> ConnectorDefinition:
        connector = self.get_connector(tenant_id, connector_id)
        if credential_ref.tenant_id != tenant_id or credential_ref.workspace_id not in {
            None,
            connector.workspace_id,
        }:
            raise ConnectorAccessDeniedError("connector credential is not in workspace")
        updated = connector.model_copy(
            update={
                "credential_ref": credential_ref,
                "status": ConnectorStatus.ENABLED,
                "updated_at": utc_now(),
            }
        )
        self.connectors[connector_id] = updated
        return updated.model_copy(deep=True)

    def update_connector_sync_state(
        self,
        tenant_id: str,
        connector_id: str,
        update: ConnectorSyncStateUpdate,
    ) -> ConnectorDefinition:
        connector = self.get_connector(tenant_id, connector_id)
        updated = connector.apply_sync_state(update)
        self.connectors[connector_id] = updated
        return updated.model_copy(deep=True)

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
