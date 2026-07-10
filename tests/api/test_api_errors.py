import json
from pathlib import Path

from fastapi.testclient import TestClient

from taroai.api.errors import ApiExceptionManager, ApiExceptionRule
from taroai.app import create_app
from taroai.connectors import ConnectorAccessDeniedError, ConnectorNotFoundError
from taroai.store import TenantAccessError
from taroai.tool_gateway import ToolApprovalRequiredError, ToolExecutionError


def test_exception_manager_maps_known_errors_to_one_response_shape():
    manager = ApiExceptionManager()

    response = manager.to_response(TenantAccessError("cross tenant"))

    assert response.status_code == 403
    body = json.loads(response.body)
    assert body == {
        "code": "tenant_access_denied",
        "message": "tenant access denied",
        "retryable": False,
        "details": {},
    }


def test_exception_manager_accepts_custom_exception_rules():
    class SkillExecutionError(RuntimeError):
        pass

    manager = ApiExceptionManager()
    manager.add_rule(
        ApiExceptionRule(
            exception_type=SkillExecutionError,
            status_code=422,
            code="skill_execution_failed",
            message="skill execution failed",
            retryable=True,
        )
    )

    response = manager.to_response(SkillExecutionError("tool timeout"))

    assert response.status_code == 422
    assert json.loads(response.body) == {
        "code": "skill_execution_failed",
        "message": "skill execution failed",
        "retryable": True,
        "details": {},
    }


def test_exception_manager_maps_tool_gateway_errors_to_one_response_shape():
    manager = ApiExceptionManager()

    execution_response = manager.to_response(ToolExecutionError("missing scopes"))
    approval_response = manager.to_response(ToolApprovalRequiredError("approval required"))

    assert execution_response.status_code == 422
    assert json.loads(execution_response.body) == {
        "code": "tool_execution_failed",
        "message": "missing scopes",
        "retryable": False,
        "details": {},
    }
    assert approval_response.status_code == 409
    assert json.loads(approval_response.body) == {
        "code": "tool_approval_required",
        "message": "approval required",
        "retryable": False,
        "details": {},
    }


def test_exception_manager_maps_connector_errors_to_one_response_shape():
    manager = ApiExceptionManager()

    denied_response = manager.to_response(ConnectorAccessDeniedError("connector is not in tenant"))
    missing_response = manager.to_response(ConnectorNotFoundError("connector not found"))

    assert denied_response.status_code == 403
    assert json.loads(denied_response.body) == {
        "code": "tenant_access_denied",
        "message": "tenant access denied",
        "retryable": False,
        "details": {},
    }
    assert missing_response.status_code == 404
    assert json.loads(missing_response.body) == {
        "code": "not_found",
        "message": "not found",
        "retryable": False,
        "details": {},
    }


def test_store_errors_are_handled_by_registered_exception_manager():
    client = TestClient(create_app())
    created = client.post(
        "/api/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"},
        json={
            "workspace_id": "workspace_sales",
            "message": "Create a prospect brief.",
            "mode": "workflow",
        },
    ).json()

    response = client.get(
        f"/api/runs/{created['run_id']}",
        headers={"X-Tenant-ID": "tenant_other", "X-User-ID": "user_2"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert response.json()["message"] == "tenant access denied"


def test_app_routes_do_not_repeat_store_exception_mapping():
    app_source = Path("apps/api/src/taroai/app.py").read_text()

    assert "map_store_error" not in app_source
    assert "except (TenantAccessError, NotFoundError)" not in app_source
