from pathlib import Path

from taroai.db import DatabaseConfig, MigrationRunner
from taroai.model_gateway import (
    ModelPolicyChangeRequestCreate,
    ModelPolicyScopeUpsert,
    SqlModelPolicyStore,
)


def test_sql_model_policy_store_persists_tenant_and_workspace_scopes(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlModelPolicyStore(config=DatabaseConfig(url=database_url))

    tenant_scope = store.upsert_scope(
        ModelPolicyScopeUpsert(
            tenant_id="tenant_acme",
            default_model="enterprise-default",
            allowed_models=["enterprise-default", "sales-approved"],
            denied_models=["consumer-free"],
            model_sensitivity_limits={"enterprise-default": 2},
            updated_by_user_id="admin_1",
        )
    )
    workspace_scope = store.upsert_scope(
        ModelPolicyScopeUpsert(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            default_model="sales-approved",
            allowed_models=["sales-approved"],
            denied_models=["consumer-free", "unreviewed-preview"],
            model_sensitivity_limits={"sales-approved": 4},
            updated_by_user_id="admin_2",
        )
    )

    restarted = SqlModelPolicyStore(config=DatabaseConfig(url=database_url))
    scopes = restarted.list_scopes("tenant_acme")
    all_scopes = restarted.list_all_scopes()

    assert tenant_scope.workspace_id is None
    assert workspace_scope.workspace_id == "workspace_sales"
    assert [scope.workspace_id for scope in scopes] == [None, "workspace_sales"]
    assert scopes[0].default_model == "enterprise-default"
    assert scopes[0].model_sensitivity_limits == {"enterprise-default": 2}
    assert scopes[1].allowed_models == ["sales-approved"]
    assert scopes[1].denied_models == ["consumer-free", "unreviewed-preview"]
    assert scopes[1].model_sensitivity_limits == {"sales-approved": 4}
    assert scopes[1].updated_by_user_id == "admin_2"
    assert scopes[1].to_policy_scope().model_sensitivity_limits == {"sales-approved": 4}
    assert [scope.tenant_id for scope in all_scopes] == ["tenant_acme", "tenant_acme"]
    assert restarted.list_scopes("tenant_other") == []


def test_sql_model_policy_store_applies_requested_scope_only_after_approval(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlModelPolicyStore(config=DatabaseConfig(url=database_url))

    requested = store.create_policy_change_request(
        ModelPolicyChangeRequestCreate(
            tenant_id="tenant_acme",
            scope_upsert=ModelPolicyScopeUpsert(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                default_model="consumer-free",
                allowed_models=["enterprise-approved"],
                denied_models=["consumer-free"],
                model_sensitivity_limits={"enterprise-approved": 4},
                updated_by_user_id="model_admin",
            ),
            requested_by_user_id="model_admin",
        )
    )

    restarted = SqlModelPolicyStore(config=DatabaseConfig(url=database_url))
    pending_requests = restarted.list_policy_change_requests("tenant_acme")

    assert requested.status == "pending"
    assert pending_requests[0].id == requested.id
    assert pending_requests[0].scope_upsert.workspace_id == "workspace_sales"
    assert restarted.list_scopes("tenant_acme") == []
    assert restarted.list_policy_change_requests("tenant_other") == []

    approved = restarted.approve_policy_change_request(
        tenant_id="tenant_acme",
        request_id=requested.id,
        reviewed_by_user_id="model_approver",
    )
    scopes = restarted.list_scopes("tenant_acme")

    assert approved.change_request.status == "approved"
    assert approved.change_request.reviewed_by_user_id == "model_approver"
    assert approved.scope_record.workspace_id == "workspace_sales"
    assert approved.scope_record.updated_by_user_id == "model_approver"
    assert scopes[0].default_model == "consumer-free"
    assert scopes[0].allowed_models == ["enterprise-approved"]
    assert scopes[0].denied_models == ["consumer-free"]


def test_sql_model_policy_store_records_version_history_for_direct_and_approved_changes(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlModelPolicyStore(config=DatabaseConfig(url=database_url))

    store.upsert_scope(
        ModelPolicyScopeUpsert(
            tenant_id="tenant_acme",
            default_model="enterprise-default",
            allowed_models=["enterprise-default"],
            updated_by_user_id="admin_1",
        )
    )
    store.upsert_scope(
        ModelPolicyScopeUpsert(
            tenant_id="tenant_acme",
            default_model="enterprise-default-v2",
            allowed_models=["enterprise-default-v2"],
            updated_by_user_id="admin_2",
        )
    )
    requested = store.create_policy_change_request(
        ModelPolicyChangeRequestCreate(
            tenant_id="tenant_acme",
            scope_upsert=ModelPolicyScopeUpsert(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                default_model="sales-approved",
                allowed_models=["sales-approved"],
                model_sensitivity_limits={"sales-approved": 4},
                updated_by_user_id="model_admin",
            ),
            requested_by_user_id="model_admin",
        )
    )
    store.approve_policy_change_request(
        tenant_id="tenant_acme",
        request_id=requested.id,
        reviewed_by_user_id="model_approver",
    )

    restarted = SqlModelPolicyStore(config=DatabaseConfig(url=database_url))
    tenant_versions = restarted.list_policy_versions("tenant_acme", workspace_id=None)
    workspace_versions = restarted.list_policy_versions(
        "tenant_acme",
        workspace_id="workspace_sales",
    )
    all_versions = restarted.list_policy_versions("tenant_acme")

    assert [version.version for version in tenant_versions] == [1, 2]
    assert [version.default_model for version in tenant_versions] == [
        "enterprise-default",
        "enterprise-default-v2",
    ]
    assert [version.change_type for version in tenant_versions] == [
        "upsert_scope",
        "upsert_scope",
    ]
    assert tenant_versions[1].created_by_user_id == "admin_2"
    assert len(workspace_versions) == 1
    assert workspace_versions[0].version == 1
    assert workspace_versions[0].workspace_id == "workspace_sales"
    assert workspace_versions[0].change_type == "approved_change_request"
    assert workspace_versions[0].change_request_id == requested.id
    assert workspace_versions[0].created_by_user_id == "model_approver"
    assert workspace_versions[0].model_sensitivity_limits == {"sales-approved": 4}
    assert [version.workspace_id for version in all_versions] == [
        None,
        None,
        "workspace_sales",
    ]
    assert restarted.list_policy_versions("tenant_other") == []
