from pathlib import Path

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.db import DatabaseConfig, SqlControlPlaneRepository
from taroai.domain import RunCreate, RunStatus
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.knowledge import InMemoryKnowledgeService
from taroai.memory import InMemoryLongTermMemoryService, InMemoryShortTermMemoryService
from taroai.storage import InMemoryStorageCatalog, S3CompatibleObjectStorage
from taroai.store import InMemoryControlPlaneStore


class CapturingS3Client:
    def __init__(self):
        self.put_calls: list[dict] = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        return {"ETag": '"etag"'}


def create_backoffice_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    reader = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="reader@example.com",
            display_name="Reader",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_backoffice_reader",
            name="Backoffice Reader",
            permissions=[
                Permission(action="audit.read", resource="tenant:tenant_acme"),
                Permission(action="billing.read", resource="tenant:tenant_acme"),
                Permission(action="skills.read", resource="tenant:tenant_acme"),
                Permission(action="skills.publish", resource="tenant:tenant_acme"),
                Permission(action="memory.read", resource="tenant:tenant_acme"),
                Permission(action="memory.write", resource="tenant:tenant_acme"),
                Permission(action="memory.review", resource="tenant:tenant_acme"),
                Permission(action="storage.read", resource="tenant:tenant_acme"),
                Permission(action="storage.write", resource="tenant:tenant_acme"),
                Permission(action="knowledge.read", resource="tenant:tenant_acme"),
                Permission(action="knowledge.write", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", reader.id, "role_backoffice_reader")
    return identity, reader


def skill_manifest(skill_id: str, name: str) -> dict:
    return {
        "id": skill_id,
        "version": "1.0.0",
        "name": name,
        "description": f"{name} skill",
        "type": "api_skill",
        "owner": "solutions",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "required_scopes": [],
        "risk_level": "low",
        "approval_required": [],
        "visibility": "tenant",
        "runtime": {"sandbox": "python"},
        "billing_meters": [],
        "tests": [],
        "evals": [],
    }


def object_storage_with_client(client: CapturingS3Client) -> S3CompatibleObjectStorage:
    return S3CompatibleObjectStorage(
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        access_key_id="access",
        secret_access_key="secret",
        client=client,
    )


def test_list_runs_returns_cursor_page_ordered_by_created_at_desc():
    client = TestClient(create_app())
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"}
    first = client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_sales", "message": "First run."},
    ).json()
    second = client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_sales", "message": "Second run."},
    ).json()
    client.post(
        "/api/runs",
        headers={"X-Tenant-ID": "tenant_other", "X-User-ID": "user_2"},
        json={"workspace_id": "workspace_sales", "message": "Other tenant run."},
    )
    third = client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_ops", "message": "Third run."},
    ).json()

    page = client.get("/api/runs?limit=2", headers=headers)
    next_page = client.get(
        f"/api/runs?limit=2&cursor={page.json()['next_cursor']}",
        headers=headers,
    )

    assert page.status_code == 200
    assert page.json()["items"][0]["id"] == third["run_id"]
    assert page.json()["items"][1]["id"] == second["run_id"]
    assert page.json()["limit"] == 2
    assert page.json()["has_more"] is True
    assert page.json()["next_cursor"] is not None
    assert next_page.status_code == 200
    assert [item["id"] for item in next_page.json()["items"]] == [first["run_id"]]
    assert next_page.json()["has_more"] is False
    assert next_page.json()["next_cursor"] is None


def test_list_runs_is_tenant_scoped_and_filterable():
    store = InMemoryControlPlaneStore()
    client = TestClient(create_app(store=store))
    client.post(
        "/api/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"},
        json={"workspace_id": "workspace_sales", "message": "Sales run."},
    )
    failed = client.post(
        "/api/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"},
        json={"workspace_id": "workspace_ops", "message": "Ops run."},
    ).json()
    client.post(
        "/api/runs",
        headers={"X-Tenant-ID": "tenant_other", "X-User-ID": "user_2"},
        json={"workspace_id": "workspace_sales", "message": "Other tenant run."},
    )
    store.update_run_status("tenant_acme", failed["run_id"], RunStatus.FAILED)

    response = client.get(
        "/api/runs?workspace_id=workspace_ops&status=failed",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [failed["run_id"]]
    assert response.json()["items"][0]["tenant_id"] == "tenant_acme"
    assert response.json()["items"][0]["workspace_id"] == "workspace_ops"
    assert response.json()["items"][0]["status"] == "failed"


def test_sql_repository_lists_runs_with_filters(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    sales = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(workspace_id="workspace_sales", message="Sales run."),
    )
    ops = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(workspace_id="workspace_ops", message="Ops run."),
    )
    repository.create_run(
        tenant_id="tenant_other",
        user_id="user_2",
        payload=RunCreate(workspace_id="workspace_other", message="Other tenant run."),
    )
    repository.update_run_status("tenant_acme", ops.id, RunStatus.FAILED)

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))

    assert [run.id for run in restarted.list_runs("tenant_acme")] == [sales.id, ops.id]
    assert [
        run.id
        for run in restarted.list_runs(
            "tenant_acme",
            workspace_id="workspace_ops",
            status=RunStatus.FAILED,
        )
    ] == [ops.id]


def test_billing_meters_support_cursor_page_response_when_requested():
    identity, reader = create_backoffice_identity()
    store = InMemoryControlPlaneStore()
    client = TestClient(create_app(identity_service=identity, store=store))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id}
    client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_sales", "message": "First run."},
    )
    second = client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_sales", "message": "Second run."},
    ).json()
    other = store.create_run(
        "tenant_other",
        "user_2",
        RunCreate(workspace_id="workspace_other", message="Other tenant run."),
    )
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=second["run_id"],
        meter_type="storage_bytes",
        quantity=128,
        unit="bytes",
    )
    store.record_billing_meter(
        tenant_id="tenant_other",
        run_id=other.id,
        meter_type="storage_bytes",
        quantity=256,
        unit="bytes",
    )

    page = client.get("/api/billing/meters?limit=2", headers=headers)
    next_page = client.get(
        f"/api/billing/meters?limit=2&cursor={page.json()['next_cursor']}",
        headers=headers,
    )

    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["has_more"] is True
    assert page.json()["next_cursor"] is not None
    assert len(page.json()["items"]) == 2
    assert all(item["tenant_id"] == "tenant_acme" for item in page.json()["items"])
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["has_more"] is False
    assert next_page.json()["next_cursor"] is None


def test_audit_events_support_cursor_page_response_when_requested():
    identity, reader = create_backoffice_identity()
    store = InMemoryControlPlaneStore()
    client = TestClient(create_app(identity_service=identity, store=store))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id}
    client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_sales", "message": "First run."},
    )
    second = client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_ops", "message": "Second run."},
    ).json()
    other = store.create_run(
        "tenant_other",
        "user_2",
        RunCreate(workspace_id="workspace_other", message="Other tenant run."),
    )
    store.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id="workspace_ops",
        user_id=reader.id,
        run_id=second["run_id"],
        event_type="storage.uploaded",
        metadata={"storage_object_id": "storage_123"},
    )
    store.record_audit_event(
        tenant_id="tenant_other",
        workspace_id="workspace_other",
        user_id="user_2",
        run_id=other.id,
        event_type="storage.uploaded",
        metadata={"storage_object_id": "storage_456"},
    )

    page = client.get(
        "/api/audit-events?limit=2&event_type=storage.uploaded",
        headers=headers,
    )

    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["has_more"] is False
    assert page.json()["next_cursor"] is None
    assert [item["event_type"] for item in page.json()["items"]] == ["storage.uploaded"]
    assert page.json()["items"][0]["tenant_id"] == "tenant_acme"
    assert page.json()["items"][0]["workspace_id"] == "workspace_ops"


def test_skills_support_cursor_page_response_when_requested():
    identity, reader = create_backoffice_identity()
    client = TestClient(create_app(identity_service=identity))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id}
    for skill_id, name in [
        ("skill_research", "Research"),
        ("skill_email", "Email"),
        ("skill_crm", "CRM"),
    ]:
        created = client.post(
            "/api/skills",
            headers=headers,
            json=skill_manifest(skill_id, name),
        )
        assert created.status_code == 201

    page = client.get("/api/skills?limit=2", headers=headers)
    next_page = client.get(
        f"/api/skills?limit=2&cursor={page.json()['next_cursor']}",
        headers=headers,
    )

    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["has_more"] is True
    assert page.json()["next_cursor"] is not None
    assert len(page.json()["items"]) == 2
    assert all(item["tenant_id"] == "tenant_acme" for item in page.json()["items"])
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["has_more"] is False


def test_long_term_memory_supports_cursor_page_response_when_requested():
    identity, reader = create_backoffice_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            long_term_memory_service=InMemoryLongTermMemoryService(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id}
    for content in [
        "Use approved renewal checklist.",
        "Escalate legal review for regulated accounts.",
        "Prefer quarterly business review format.",
    ]:
        candidate = client.post(
            "/api/memory/candidates",
            headers=headers,
            json={
                "workspace_id": "workspace_sales",
                "scope_type": "team",
                "scope_id": "team_sales",
                "source_run_id": "run_123",
                "content": content,
                "metadata": {"source": "run_summary"},
            },
        )
        assert candidate.status_code == 201
        approved = client.post(
            f"/api/memory/{candidate.json()['id']}/approve",
            headers=headers,
        )
        assert approved.status_code == 200

    page = client.get(
        "/api/memory?scope_type=team&scope_id=team_sales&limit=2",
        headers=headers,
    )
    next_page = client.get(
        f"/api/memory?scope_type=team&scope_id=team_sales&limit=2&cursor={page.json()['next_cursor']}",
        headers=headers,
    )

    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["has_more"] is True
    assert len(page.json()["items"]) == 2
    assert all(item["tenant_id"] == "tenant_acme" for item in page.json()["items"])
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["has_more"] is False


def test_short_term_memory_supports_cursor_page_response_when_requested():
    identity, reader = create_backoffice_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            short_term_memory_service=InMemoryShortTermMemoryService(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id}
    for key in ["planner.scratchpad", "tool.last_result", "draft.summary"]:
        created = client.post(
            "/api/memory/short-term",
            headers=headers,
            json={
                "workspace_id": "workspace_sales",
                "run_id": "run_123",
                "key": key,
                "value": {"key": key},
                "ttl_seconds": 60,
            },
        )
        assert created.status_code == 201

    page = client.get(
        "/api/memory/short-term?run_id=run_123&limit=2",
        headers=headers,
    )
    next_page = client.get(
        f"/api/memory/short-term?run_id=run_123&limit=2&cursor={page.json()['next_cursor']}",
        headers=headers,
    )

    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["has_more"] is True
    assert len(page.json()["items"]) == 2
    assert all(item["tenant_id"] == "tenant_acme" for item in page.json()["items"])
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["has_more"] is False


def test_artifacts_support_cursor_page_response_when_requested():
    store = InMemoryControlPlaneStore()
    client = TestClient(create_app(store=store))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"}
    run = client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_sales", "message": "Create artifacts."},
    ).json()
    for name in ["brief.md", "table.csv", "summary.txt"]:
        store.create_artifact(
            tenant_id="tenant_acme",
            run_id=run["run_id"],
            name=name,
            artifact_type="file",
            uri=f"s3://bucket/{name}",
        )

    page = client.get(
        f"/api/runs/{run['run_id']}/artifacts?limit=2",
        headers=headers,
    )
    next_page = client.get(
        f"/api/runs/{run['run_id']}/artifacts?limit=2&cursor={page.json()['next_cursor']}",
        headers=headers,
    )

    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["has_more"] is True
    assert len(page.json()["items"]) == 2
    assert all(item["tenant_id"] == "tenant_acme" for item in page.json()["items"])
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["has_more"] is False


def test_storage_objects_support_cursor_page_response_when_requested():
    identity, reader = create_backoffice_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id}
    run = client.post(
        "/api/runs",
        headers=headers,
        json={"workspace_id": "workspace_sales", "message": "Create files."},
    ).json()
    for filename in ["brief.md", "table.csv", "summary.txt"]:
        created = client.post(
            "/api/storage/objects",
            headers=headers,
            json={
                "workspace_id": "workspace_sales",
                "run_id": run["run_id"],
                "purpose": "artifacts",
                "filename": filename,
                "content_type": "text/plain",
                "size_bytes": 10,
            },
        )
        assert created.status_code == 201

    page = client.get(
        f"/api/runs/{run['run_id']}/storage-objects?limit=2",
        headers=headers,
    )
    next_page = client.get(
        f"/api/runs/{run['run_id']}/storage-objects?limit=2&cursor={page.json()['next_cursor']}",
        headers=headers,
    )

    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["has_more"] is True
    assert len(page.json()["items"]) == 2
    assert all(item["tenant_id"] == "tenant_acme" for item in page.json()["items"])
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["has_more"] is False


def test_knowledge_bases_and_documents_support_cursor_page_response_when_requested():
    identity, reader = create_backoffice_identity()
    storage_client = CapturingS3Client()
    client = TestClient(
        create_app(
            identity_service=identity,
            knowledge_service=InMemoryKnowledgeService(),
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
            object_storage=object_storage_with_client(storage_client),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id}
    base_ids: list[str] = []
    for name in ["Sales", "Support", "Finance"]:
        created = client.post(
            "/api/knowledge-bases",
            headers=headers,
            json={
                "workspace_id": "workspace_sales",
                "name": name,
                "description": f"{name} docs",
            },
        )
        assert created.status_code == 201
        base_ids.append(created.json()["id"])
    for index, title in enumerate(["Playbook", "Checklist", "FAQ"], start=1):
        created = client.post(
            "/api/knowledge-documents",
            headers=headers,
            json={
                "workspace_id": "workspace_sales",
                "knowledge_base_id": base_ids[0],
                "source_uri": f"https://example.com/doc-{index}",
                "source_document_id": f"doc-{index}",
                "title": title,
                "content": f"{title} content",
                "document_version": "1.0",
                "content_hash": f"hash-{index}",
                "chunks": [{"content": f"{title} content"}],
            },
        )
        assert created.status_code == 201

    bases = client.get("/api/knowledge-bases?limit=2", headers=headers)
    next_bases = client.get(
        f"/api/knowledge-bases?limit=2&cursor={bases.json()['next_cursor']}",
        headers=headers,
    )
    documents = client.get(
        f"/api/knowledge-documents?knowledge_base_id={base_ids[0]}&limit=2",
        headers=headers,
    )
    next_documents = client.get(
        f"/api/knowledge-documents?knowledge_base_id={base_ids[0]}&limit=2&cursor={documents.json()['next_cursor']}",
        headers=headers,
    )

    assert bases.status_code == 200
    assert bases.json()["limit"] == 2
    assert bases.json()["has_more"] is True
    assert len(bases.json()["items"]) == 2
    assert next_bases.status_code == 200
    assert len(next_bases.json()["items"]) == 1
    assert documents.status_code == 200
    assert documents.json()["limit"] == 2
    assert documents.json()["has_more"] is True
    assert len(documents.json()["items"]) == 2
    assert next_documents.status_code == 200
    assert len(next_documents.json()["items"]) == 1
