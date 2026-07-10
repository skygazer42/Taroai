from pathlib import Path

import pytest

from taroai.connectors import (
    ConnectorAuthMode,
    ConnectorCapability,
    ConnectorCredentialRef,
    ConnectorDefinitionCreate,
    ConnectorStatus,
    ConnectorType,
    SqlConnectorRegistry,
)
import taroai.connectors as connectors_module
from taroai.db import DatabaseConfig, MigrationRunner


def prepare_database(path: Path) -> DatabaseConfig:
    config = DatabaseConfig(url=f"sqlite:///{path}")
    MigrationRunner(
        config=config,
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    return config


def connector_payload(**overrides) -> ConnectorDefinitionCreate:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
        "type": ConnectorType.SAAS,
        "display_name": "Sales CRM",
        "owner_user_id": "user_admin",
        "auth_mode": ConnectorAuthMode.API_KEY,
        "credential_ref": ConnectorCredentialRef(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            secret_ref_id="secret_crm_api_key",
            required_actions=["connector.read"],
        ),
        "capabilities": [
            ConnectorCapability(
                name="search_accounts",
                required_scopes=["crm.accounts.read"],
                risk_level="medium",
            )
        ],
        "sensitivity_level": 2,
        "metadata": {"provider": "crm"},
    }
    data.update(overrides)
    return ConnectorDefinitionCreate(**data)


def test_sql_connector_registry_persists_definitions_without_credential_values(tmp_path):
    config = prepare_database(tmp_path / "connectors.sqlite3")
    first_registry = SqlConnectorRegistry(config=config)

    connector = first_registry.register_connector(connector_payload())
    second_registry = SqlConnectorRegistry(config=config)
    loaded = second_registry.get_connector("tenant_acme", connector.id)

    assert loaded.id == connector.id
    assert loaded.tenant_id == "tenant_acme"
    assert loaded.workspace_id == "workspace_sales"
    assert loaded.credential_ref is not None
    assert loaded.credential_ref.secret_ref_id == "secret_crm_api_key"
    assert loaded.capabilities[0].name == "search_accounts"
    assert loaded.metadata == {"provider": "crm"}
    assert "api-key-value" not in str(loaded.model_dump(mode="json"))


def test_sql_connector_registry_updates_definition_and_status(tmp_path):
    config = prepare_database(tmp_path / "connectors-update.sqlite3")
    first_registry = SqlConnectorRegistry(config=config)
    connector = first_registry.register_connector(connector_payload())

    updated = first_registry.update_connector(
        tenant_id="tenant_acme",
        connector_id=connector.id,
        update=connectors_module.ConnectorUpdateRequest(
            display_name="Enterprise CRM",
            sensitivity_level=4,
            metadata={"provider": "crm", "tier": "enterprise"},
            capabilities=[
                ConnectorCapability(
                    name="search_accounts",
                    required_scopes=["crm.accounts.read"],
                    risk_level="medium",
                )
            ],
        ),
    )
    disabled = first_registry.update_connector_status(
        tenant_id="tenant_acme",
        connector_id=connector.id,
        status=ConnectorStatus.DISABLED,
    )
    second_registry = SqlConnectorRegistry(config=config)
    loaded = second_registry.get_connector("tenant_acme", connector.id)

    assert updated.display_name == "Enterprise CRM"
    assert updated.metadata == {"provider": "crm", "tier": "enterprise"}
    assert updated.sensitivity_level == 4
    assert updated.capabilities[0].required_scopes == ["crm.accounts.read"]
    assert disabled.status == ConnectorStatus.DISABLED
    assert loaded.display_name == "Enterprise CRM"
    assert loaded.status == ConnectorStatus.DISABLED
    assert loaded.updated_at >= connector.updated_at


def test_sql_connector_registry_persists_sync_state(tmp_path):
    config = prepare_database(tmp_path / "connectors-sync-state.sqlite3")
    first_registry = SqlConnectorRegistry(config=config)
    connector = first_registry.register_connector(connector_payload())

    updated = first_registry.update_connector_sync_state(
        tenant_id="tenant_acme",
        connector_id=connector.id,
        update=connectors_module.ConnectorSyncStateUpdate(
            status=connectors_module.ConnectorSyncStatus.PENDING,
            run_id="run_sync_1",
            job_id="job_sync_1",
            knowledge_base_id="knowledge_sales",
            cursor="cursor_001",
        ),
    )
    second_registry = SqlConnectorRegistry(config=config)
    loaded = second_registry.get_connector("tenant_acme", connector.id)

    assert updated.sync_state is not None
    assert updated.sync_state.status == connectors_module.ConnectorSyncStatus.PENDING
    assert loaded.sync_state is not None
    assert loaded.sync_state.run_id == "run_sync_1"
    assert loaded.sync_state.job_id == "job_sync_1"
    assert loaded.sync_state.knowledge_base_id == "knowledge_sales"
    assert loaded.sync_state.cursor == "cursor_001"


def test_sql_connector_registry_enforces_tenant_scope(tmp_path):
    config = prepare_database(tmp_path / "connectors-scope.sqlite3")
    registry = SqlConnectorRegistry(config=config)
    connector = registry.register_connector(connector_payload())

    assert registry.list_connectors("tenant_other") == []
    with pytest.raises(PermissionError):
        registry.get_connector("tenant_other", connector.id)
