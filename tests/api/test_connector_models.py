import pytest
from pydantic import ValidationError

from taroai.connectors import (
    ConnectorAuthMode,
    ConnectorCapability,
    ConnectorCredentialRef,
    ConnectorDefinitionCreate,
    ConnectorStatus,
    ConnectorType,
    InMemoryConnectorRegistry,
)


def test_connector_definition_is_tenant_scoped_and_uses_secret_reference_only():
    registry = InMemoryConnectorRegistry()

    connector = registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.SAAS,
            display_name="Sales CRM",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.OAUTH2,
            credential_ref=ConnectorCredentialRef(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                secret_ref_id="secret_crm_oauth",
                required_actions=["connector.read", "connector.write"],
            ),
            capabilities=[
                ConnectorCapability(
                    name="search_accounts",
                    required_scopes=["crm.accounts.read"],
                    risk_level="medium",
                    approval_required=False,
                ),
            ],
            sensitivity_level=2,
        )
    )

    assert connector.tenant_id == "tenant_acme"
    assert connector.workspace_id == "workspace_sales"
    assert connector.status == ConnectorStatus.DRAFT
    assert connector.credential_ref is not None
    assert connector.credential_ref.secret_ref_id == "secret_crm_oauth"
    assert "oauth-token-value" not in str(connector.model_dump(mode="json"))


def test_connector_definition_rejects_raw_credential_fields():
    with pytest.raises(ValidationError):
        ConnectorCredentialRef(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            secret_ref_id="secret_crm_oauth",
            raw_token="oauth-token-value",
        )


def test_connector_registry_blocks_cross_tenant_and_cross_workspace_credentials():
    registry = InMemoryConnectorRegistry()

    with pytest.raises(PermissionError):
        registry.register_connector(
            ConnectorDefinitionCreate(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                type=ConnectorType.SAAS,
                display_name="Sales CRM",
                owner_user_id="user_admin",
                auth_mode=ConnectorAuthMode.API_KEY,
                credential_ref=ConnectorCredentialRef(
                    tenant_id="tenant_other",
                    workspace_id="workspace_sales",
                    secret_ref_id="secret_other",
                ),
                capabilities=[],
            )
        )

    with pytest.raises(PermissionError):
        registry.register_connector(
            ConnectorDefinitionCreate(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                type=ConnectorType.SAAS,
                display_name="Sales CRM",
                owner_user_id="user_admin",
                auth_mode=ConnectorAuthMode.API_KEY,
                credential_ref=ConnectorCredentialRef(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_finance",
                    secret_ref_id="secret_finance",
                ),
                capabilities=[],
            )
        )


def test_connector_registry_lists_only_tenant_connectors():
    registry = InMemoryConnectorRegistry()
    first = registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.INTERNAL_API,
            display_name="Internal Orders API",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.NONE,
            capabilities=[],
        )
    )
    registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_other",
            workspace_id="workspace_sales",
            type=ConnectorType.INTERNAL_API,
            display_name="Other Tenant API",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.NONE,
            capabilities=[],
        )
    )

    assert [connector.id for connector in registry.list_connectors("tenant_acme")] == [
        first.id,
    ]
