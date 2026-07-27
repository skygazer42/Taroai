from pathlib import Path

from taroai.app import create_app


MVP_ROUTE_CONTRACT = [
    ("POST", "/api/auth/login", "auth/session"),
    ("POST", "/api/auth/logout", "auth/session"),
    ("POST", "/api/tenants/bootstrap", "tenant onboarding"),
    ("GET", "/api/tenants/current/readiness", "tenant onboarding"),
    ("GET", "/api/runs", "run control plane"),
    ("POST", "/api/runs", "run control plane"),
    ("GET", "/api/runs/{run_id}", "run control plane"),
    ("POST", "/api/runs/{run_id}/execute", "agent runtime"),
    ("GET", "/api/runs/{run_id}/events", "run event stream"),
    ("GET", "/api/runs/{run_id}/state", "agent runtime"),
    ("POST", "/api/runs/{run_id}/cancel", "run control plane"),
    ("POST", "/api/runs/{run_id}/retry", "run control plane"),
    ("POST", "/api/runs/{run_id}/approvals", "approval control"),
    ("POST", "/api/runs/{run_id}/approvals/reject", "approval control"),
    ("GET", "/api/runs/{run_id}/artifacts", "artifact delivery"),
    ("GET", "/api/runs/{run_id}/storage-objects", "artifact delivery"),
    ("POST", "/api/knowledge/query", "knowledge retrieval"),
    ("GET", "/api/billing/meters", "billing"),
    ("GET", "/api/audit-events", "audit"),
    ("GET", "/api/skills", "skill registry"),
    ("POST", "/api/skills", "skill registry"),
    ("GET", "/api/skills/{skill_id}", "skill registry"),
    ("POST", "/api/skills/{skill_id}/publish", "skill registry"),
    ("POST", "/api/skills/{skill_id}/disable", "skill registry"),
    ("GET", "/api/skills/{skill_id}/versions", "skill registry"),
    ("GET", "/api/workspaces/{workspace_id}/skills", "workspace skills"),
    (
        "POST",
        "/api/workspaces/{workspace_id}/skills/{skill_id}/install",
        "workspace skills",
    ),
    (
        "POST",
        "/api/workspaces/{workspace_id}/skills/{skill_id}/invoke",
        "workspace skills",
    ),
]


def test_mvp_openapi_contract_exposes_required_routes():
    schema = create_app().openapi()

    missing = []
    for method, path, _owner in MVP_ROUTE_CONTRACT:
        operations = schema["paths"].get(path, {})
        if method.lower() not in operations:
            missing.append(f"{method} {path}")

    assert missing == []


def test_mvp_openapi_contract_keeps_unversioned_paths_until_versioning_migration():
    schema = create_app().openapi()

    # 内部 API 在版本化迁移前保持无版本；对外公开的 Agent App 接口
    # (/api/v1/apps/) 是有意从第一天就带版本的例外。
    assert not [
        path
        for path in schema["paths"]
        if path.startswith("/api/v1/") and not path.startswith("/api/v1/apps/")
    ]


def test_mvp_api_contract_checklist_documents_routes_and_owners():
    checklist = Path("docs/mvp/api-contract-checklist.md")

    text = checklist.read_text(encoding="utf-8")

    assert "# MVP API Contract Checklist" in text
    assert "Route Ownership" in text
    assert "/api/v1 migration" in text
    for method, path, owner in MVP_ROUTE_CONTRACT:
        assert f"`{method} {path}`" in text
        assert owner in text
