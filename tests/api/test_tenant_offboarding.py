import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.domain import utc_now
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.lifecycle import (
    DataCategory,
    InMemoryTenantOffboardingStore,
    InMemoryLifecyclePolicyStore,
    LegalHoldCreate,
    LegalHoldScopeType,
    SqlTenantOffboardingStore,
    TenantOffboardingApprovalRequest,
    TenantOffboardingApprovalStatus,
    TenantOffboardingDeletionRequest,
    TenantOffboardingDeletionService,
    TenantOffboardingExportCompletionRequest,
    TenantOffboardingRequest,
    TenantOffboardingService,
    TenantOffboardingState,
    TenantOffboardingTransitionError,
)
from taroai.knowledge import (
    DocumentChunkCreate,
    InMemoryKnowledgeService,
    KnowledgeBaseCreate,
    KnowledgeDocumentCreate,
    RetrievalRequest,
)
from taroai.memory import (
    InMemoryLongTermMemoryService,
    InMemoryShortTermMemoryService,
    MemoryScopeType,
    MemoryStatus,
    MemoryWriteRequest,
    ShortTermMemoryWrite,
)
from taroai.storage import (
    InMemoryStorageCatalog,
    S3CompatibleObjectStorage,
    StorageObjectCreate,
    StoragePurpose,
)
from taroai.store import InMemoryControlPlaneStore, NotFoundError


class RecordingObjectStorageClient:
    def __init__(self):
        self.deleted_objects: list[dict] = []
        self.put_objects: list[dict] = []

    def put_object(self, **kwargs):
        self.put_objects.append(kwargs)
        return {"ETag": '"offboarding-export-etag"'}

    def delete_object(self, **kwargs):
        self.deleted_objects.append(kwargs)
        return {"DeleteMarker": True}


def create_offboarding_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="offboarding-admin@example.com",
            display_name="Offboarding Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_offboarding_admin",
            name="Offboarding Admin",
            permissions=[
                Permission(action="lifecycle.manage", resource="tenant:tenant_acme"),
                Permission(action="lifecycle.read", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_offboarding_admin")
    return identity, account


def test_tenant_offboarding_plan_requires_approval_before_export_or_delete():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
    )

    plan = service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=True,
        )
    )

    assert plan.state == TenantOffboardingState.REQUESTED
    assert plan.approval_required is True
    assert plan.approval_status == "pending"
    assert plan.next_state_after_approval == TenantOffboardingState.EXPORT_PENDING
    assert plan.export_before_delete is True
    assert plan.reason_length == len("customer requested account closure")
    assert "storage_object" in [category.value for category in plan.categories]
    assert service.get_plan("tenant_acme", plan.id) == plan


def test_tenant_offboarding_approval_moves_plan_to_next_state():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
    )
    plan = service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=True,
        )
    )

    approved = service.approve_plan(
        TenantOffboardingApprovalRequest(
            tenant_id="tenant_acme",
            plan_id=plan.id,
            approved_by_user_id="owner_1",
        )
    )

    assert approved.state == TenantOffboardingState.EXPORT_PENDING
    assert approved.approval_required is False
    assert approved.approval_status == TenantOffboardingApprovalStatus.APPROVED
    assert approved.approved_by_user_id == "owner_1"
    assert approved.approved_at is not None
    assert service.get_plan("tenant_acme", plan.id) == approved


def test_tenant_offboarding_export_completion_records_bundle_and_moves_to_deletion_pending():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
    )
    plan = service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=True,
            categories=[DataCategory.STORAGE_OBJECT],
        )
    )
    approved = service.approve_plan(
        TenantOffboardingApprovalRequest(
            tenant_id="tenant_acme",
            plan_id=plan.id,
            approved_by_user_id="owner_1",
        )
    )

    completed = service.complete_export(
        TenantOffboardingExportCompletionRequest(
            tenant_id="tenant_acme",
            plan_id=approved.id,
            completed_by_user_id="export_worker",
            export_bundle_id="data_export_bundle_123",
            export_storage_object_id="storage_export_123",
        )
    )

    assert completed.state == TenantOffboardingState.DELETION_PENDING
    assert completed.export_bundle_id == "data_export_bundle_123"
    assert completed.export_storage_object_id == "storage_export_123"
    assert completed.export_completed_at is not None
    assert completed.updated_at >= approved.updated_at
    assert service.get_plan("tenant_acme", plan.id) == completed


def test_tenant_offboarding_export_completion_requires_export_pending_plan():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=InMemoryTenantOffboardingStore(),
    )
    plan = service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=True,
            categories=[DataCategory.STORAGE_OBJECT],
        )
    )

    with pytest.raises(TenantOffboardingTransitionError):
        service.complete_export(
            TenantOffboardingExportCompletionRequest(
                tenant_id="tenant_acme",
                plan_id=plan.id,
                completed_by_user_id="export_worker",
                export_bundle_id="data_export_bundle_123",
                export_storage_object_id="storage_export_123",
            )
        )


def test_tenant_offboarding_deletion_deletes_storage_objects_and_marks_plan_deleted():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    planning_service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
    )
    plan = planning_service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=False,
            categories=[DataCategory.STORAGE_OBJECT],
        )
    )
    approved = planning_service.approve_plan(
        TenantOffboardingApprovalRequest(
            tenant_id="tenant_acme",
            plan_id=plan.id,
            approved_by_user_id="owner_1",
        )
    )
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    first_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_sales",
            purpose=StoragePurpose.ARTIFACT,
            filename="sales-summary.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    second_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_hr",
            run_id="run_hr",
            purpose=StoragePurpose.ARTIFACT,
            filename="hr-summary.md",
            content_type="text/markdown",
            size_bytes=256,
        )
    )
    catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            run_id="run_other",
            purpose=StoragePurpose.ARTIFACT,
            filename="other.md",
            content_type="text/markdown",
            size_bytes=64,
        )
    )
    storage_client = RecordingObjectStorageClient()
    deletion_service = TenantOffboardingDeletionService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
        storage_catalog=catalog,
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=storage_client,
        ),
    )

    result = deletion_service.execute(
        TenantOffboardingDeletionRequest(
            tenant_id="tenant_acme",
            plan_id=approved.id,
            deleted_by_user_id="owner_1",
        )
    )

    assert result.plan.state == TenantOffboardingState.DELETED
    assert result.plan.deleted_by_user_id == "owner_1"
    assert result.plan.deleted_at is not None
    expected_deleted_ids = [
        storage_object.id
        for storage_object in sorted(
            [first_object, second_object],
            key=lambda storage_object: (storage_object.created_at, storage_object.id),
        )
    ]
    assert result.deleted_storage_object_ids == expected_deleted_ids
    assert result.deleted_count == 2
    assert len(storage_client.deleted_objects) == 2
    with pytest.raises(NotFoundError):
        catalog.get("tenant_acme", first_object.id)
    with pytest.raises(NotFoundError):
        catalog.get("tenant_acme", second_object.id)


def test_tenant_offboarding_deletion_blocks_without_deleting_when_legal_hold_is_active():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    planning_service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
    )
    plan = planning_service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=False,
            categories=[DataCategory.STORAGE_OBJECT],
        )
    )
    approved = planning_service.approve_plan(
        TenantOffboardingApprovalRequest(
            tenant_id="tenant_acme",
            plan_id=plan.id,
            approved_by_user_id="owner_1",
        )
    )
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_sales",
            purpose=StoragePurpose.ARTIFACT,
            filename="sales-summary.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    hold = lifecycle_store.create_legal_hold(
        LegalHoldCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            scope_type=LegalHoldScopeType.STORAGE_OBJECT,
            scope_id=storage_object.id,
            reason="regulatory preservation",
            created_by_user_id="legal_admin",
            expires_at=utc_now() + timedelta(days=30),
        )
    )
    storage_client = RecordingObjectStorageClient()
    deletion_service = TenantOffboardingDeletionService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
        storage_catalog=catalog,
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=storage_client,
        ),
    )

    result = deletion_service.execute(
        TenantOffboardingDeletionRequest(
            tenant_id="tenant_acme",
            plan_id=approved.id,
            deleted_by_user_id="owner_1",
        )
    )

    assert result.plan.state == TenantOffboardingState.BLOCKED
    assert result.plan.blocked_reason == "active_legal_hold"
    assert result.plan.blocking_legal_hold_ids == [hold.id]
    assert result.deleted_count == 0
    assert result.skipped_storage_object_ids == [storage_object.id]
    assert storage_client.deleted_objects == []
    assert catalog.get("tenant_acme", storage_object.id) == storage_object


def test_tenant_offboarding_deletion_expires_long_memory_and_clears_short_memory():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    planning_service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
    )
    plan = planning_service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=False,
            categories=[DataCategory.MEMORY],
        )
    )
    approved = planning_service.approve_plan(
        TenantOffboardingApprovalRequest(
            tenant_id="tenant_acme",
            plan_id=plan.id,
            approved_by_user_id="owner_1",
        )
    )
    long_memory = InMemoryLongTermMemoryService()
    short_memory = InMemoryShortTermMemoryService()
    active_memory = long_memory.write(
        MemoryWriteRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            scope_type=MemoryScopeType.COMPANY,
            scope_id="tenant_acme",
            source_run_id="run_123",
            content="Customer-specific memory.",
            created_by="user_1",
            metadata={"source": "conversation"},
        )
    )
    candidate_memory = long_memory.propose_candidate(
        MemoryWriteRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            scope_type=MemoryScopeType.TEAM,
            scope_id="team_sales",
            source_run_id="run_456",
            content="Candidate memory.",
            created_by="user_1",
        )
    )
    other_memory = long_memory.write(
        MemoryWriteRequest(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            scope_type=MemoryScopeType.COMPANY,
            scope_id="tenant_other",
            source_run_id="run_other",
            content="Other tenant memory.",
            created_by="user_2",
        )
    )
    now = utc_now()
    short_memory.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "delete memory"},
        ),
        now=now,
    )
    short_memory.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_456",
            key="tool.last_result",
            value={"count": 3},
        ),
        now=now,
    )
    short_memory.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            run_id="run_other",
            key="planner.scratchpad",
            value={"next": "keep"},
        ),
        now=now,
    )
    deletion_service = TenantOffboardingDeletionService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
        storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=RecordingObjectStorageClient(),
        ),
        long_term_memory_service=long_memory,
        short_term_memory_service=short_memory,
    )

    result = deletion_service.execute(
        TenantOffboardingDeletionRequest(
            tenant_id="tenant_acme",
            plan_id=approved.id,
            deleted_by_user_id="owner_1",
        )
    )

    assert result.plan.state == TenantOffboardingState.DELETED
    assert result.deleted_memory_record_ids == [active_memory.id, candidate_memory.id]
    assert result.deleted_memory_record_count == 2
    assert result.deleted_short_term_memory_count == 2
    assert long_memory.get("tenant_acme", active_memory.id).status == MemoryStatus.EXPIRED
    assert long_memory.get("tenant_acme", active_memory.id).content == ""
    assert long_memory.get("tenant_acme", candidate_memory.id).status == MemoryStatus.EXPIRED
    assert long_memory.get("tenant_other", other_memory.id) == other_memory
    assert short_memory.list_for_run("tenant_acme", "run_123", now=now) == []
    assert short_memory.list_for_run("tenant_acme", "run_456", now=now) == []
    assert [entry.key for entry in short_memory.list_for_run("tenant_other", "run_other", now=now)] == [
        "planner.scratchpad"
    ]


def test_tenant_offboarding_deletion_removes_knowledge_bases_documents_and_chunks():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    planning_service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
    )
    plan = planning_service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=False,
            categories=[DataCategory.KNOWLEDGE],
        )
    )
    approved = planning_service.approve_plan(
        TenantOffboardingApprovalRequest(
            tenant_id="tenant_acme",
            plan_id=plan.id,
            approved_by_user_id="owner_1",
        )
    )
    knowledge_service = InMemoryKnowledgeService()
    knowledge_base = knowledge_service.create_base(
        tenant_id="tenant_acme",
        user_id="user_1",
        request=KnowledgeBaseCreate(workspace_id="workspace_sales", name="Sales Playbook"),
    )
    document = knowledge_service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            knowledge_base_id=knowledge_base.id,
            source_uri="s3://tenant_acme/playbooks/sales.md",
            source_document_id="sales_doc",
            uploaded_by_user_id="user_1",
            title="Sales Playbook",
            acl_subjects=["team:sales"],
            sensitivity_level=1,
            document_version="v1",
            content_hash="hash_sales_offboarding",
            chunks=[DocumentChunkCreate(content="Renewal playbook guidance.")],
        )
    )
    other_base = knowledge_service.create_base(
        tenant_id="tenant_other",
        user_id="user_2",
        request=KnowledgeBaseCreate(workspace_id="workspace_other", name="Other Playbook"),
    )
    other_document = knowledge_service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            knowledge_base_id=other_base.id,
            source_uri="s3://tenant_other/playbooks/other.md",
            source_document_id="other_doc",
            uploaded_by_user_id="user_2",
            title="Other Playbook",
            acl_subjects=["team:other"],
            sensitivity_level=0,
            document_version="v1",
            content_hash="hash_other_offboarding",
            chunks=[DocumentChunkCreate(content="Other tenant guidance.")],
        )
    )
    deletion_service = TenantOffboardingDeletionService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=offboarding_store,
        storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=RecordingObjectStorageClient(),
        ),
        knowledge_service=knowledge_service,
    )

    result = deletion_service.execute(
        TenantOffboardingDeletionRequest(
            tenant_id="tenant_acme",
            plan_id=approved.id,
            deleted_by_user_id="owner_1",
        )
    )

    assert result.plan.state == TenantOffboardingState.DELETED
    assert result.deleted_knowledge_base_ids == [knowledge_base.id]
    assert result.deleted_knowledge_document_ids == [document.id]
    assert result.deleted_knowledge_base_count == 1
    assert result.deleted_knowledge_document_count == 1
    assert result.deleted_knowledge_chunk_count == 1
    with pytest.raises(NotFoundError):
        knowledge_service.list_chunks("tenant_acme", document.id)
    assert knowledge_service.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="renewal",
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["team:sales"],
            clearance_level=1,
        )
    ) == []
    assert [chunk.id for chunk in knowledge_service.list_chunks("tenant_other", other_document.id)]


def test_tenant_offboarding_plan_can_skip_export_when_policy_allows_direct_deletion():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=InMemoryTenantOffboardingStore(),
    )

    plan = service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="duplicate tenant",
            export_before_delete=False,
        )
    )

    assert plan.state == TenantOffboardingState.REQUESTED
    assert plan.next_state_after_approval == TenantOffboardingState.DELETION_PENDING


def test_tenant_offboarding_plan_is_blocked_by_active_tenant_legal_hold():
    lifecycle_store = InMemoryLifecyclePolicyStore()
    hold = lifecycle_store.create_legal_hold(
        LegalHoldCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            scope_type=LegalHoldScopeType.TENANT,
            scope_id="tenant_acme",
            reason="regulatory preservation",
            created_by_user_id="legal_admin",
            expires_at=utc_now() + timedelta(days=30),
        )
    )
    service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=InMemoryTenantOffboardingStore(),
    )

    plan = service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=True,
        )
    )

    assert plan.state == TenantOffboardingState.BLOCKED
    assert plan.approval_required is False
    assert plan.blocked_reason == "active_legal_hold"
    assert plan.blocking_legal_hold_ids == [hold.id]
    assert plan.next_state_after_approval is None


def test_sql_tenant_offboarding_store_persists_plan_and_approval(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'offboarding.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path="apps/api/migrations",
    ).apply()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    sql_store = SqlTenantOffboardingStore(config=DatabaseConfig(url=database_url))
    service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=sql_store,
    )
    plan = service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=False,
        )
    )
    restarted = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=SqlTenantOffboardingStore(config=DatabaseConfig(url=database_url)),
    )

    persisted = restarted.get_plan("tenant_acme", plan.id)
    approved = restarted.approve_plan(
        TenantOffboardingApprovalRequest(
            tenant_id="tenant_acme",
            plan_id=plan.id,
            approved_by_user_id="owner_1",
        )
    )

    assert persisted == plan
    assert approved.state == TenantOffboardingState.DELETION_PENDING
    assert restarted.get_plan("tenant_acme", plan.id) == approved


def test_sql_tenant_offboarding_store_persists_export_completion(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'offboarding-export.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path="apps/api/migrations",
    ).apply()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    service = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=SqlTenantOffboardingStore(config=DatabaseConfig(url=database_url)),
    )
    plan = service.create_plan(
        TenantOffboardingRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            reason="customer requested account closure",
            export_before_delete=True,
            categories=[DataCategory.STORAGE_OBJECT],
        )
    )
    approved = service.approve_plan(
        TenantOffboardingApprovalRequest(
            tenant_id="tenant_acme",
            plan_id=plan.id,
            approved_by_user_id="owner_1",
        )
    )
    completed = service.complete_export(
        TenantOffboardingExportCompletionRequest(
            tenant_id="tenant_acme",
            plan_id=approved.id,
            completed_by_user_id="export_worker",
            export_bundle_id="data_export_bundle_123",
            export_storage_object_id="storage_export_123",
        )
    )
    restarted = TenantOffboardingService(
        lifecycle_policy_store=lifecycle_store,
        offboarding_store=SqlTenantOffboardingStore(config=DatabaseConfig(url=database_url)),
    )

    assert restarted.get_plan("tenant_acme", plan.id) == completed


def test_tenant_offboarding_api_returns_plan_and_records_summary_audit():
    identity, admin = create_offboarding_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    response = client.post(
        "/api/lifecycle/tenant-offboarding-requests",
        json={
            "reason": "customer requested account closure",
            "export_before_delete": True,
        },
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 201
    assert response.json()["tenant_id"] == "tenant_acme"
    assert response.json()["state"] == "requested"
    assert response.json()["approval_required"] is True
    offboarding_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.offboarding.requested"
    ]
    assert len(offboarding_events) == 1
    assert offboarding_events[0]["metadata"]["state"] == "requested"
    assert offboarding_events[0]["metadata"]["approval_required"] is True
    assert offboarding_events[0]["metadata"]["reason_length"] == len(
        "customer requested account closure"
    )
    assert "reason" not in offboarding_events[0]["metadata"]
    assert "blocking_legal_hold_ids" not in offboarding_events[0]["metadata"]


def test_tenant_offboarding_api_gets_and_approves_plan_with_summary_audit():
    identity, admin = create_offboarding_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
            tenant_offboarding_store=offboarding_store,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    created = client.post(
        "/api/lifecycle/tenant-offboarding-requests",
        json={
            "reason": "customer requested account closure",
            "export_before_delete": True,
        },
        headers=headers,
    )

    fetched = client.get(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}",
        headers=headers,
    )
    approved = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/approve",
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert fetched.status_code == 200
    assert fetched.json()["id"] == created.json()["id"]
    assert approved.status_code == 200
    assert approved.json()["state"] == "export_pending"
    assert approved.json()["approval_status"] == "approved"
    approved_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.offboarding.approved"
    ]
    assert len(approved_events) == 1
    assert approved_events[0]["metadata"]["state"] == "export_pending"
    assert approved_events[0]["metadata"]["approved_by_user_id"] == admin.id
    assert "reason" not in approved_events[0]["metadata"]


def test_tenant_offboarding_api_runs_export_bundle_and_records_summary_audit():
    identity, admin = create_offboarding_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    first_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_sales",
            purpose=StoragePurpose.ARTIFACT,
            filename="sales-summary.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    second_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_hr",
            run_id="run_hr",
            purpose=StoragePurpose.ARTIFACT,
            filename="hr-summary.md",
            content_type="text/markdown",
            size_bytes=256,
        )
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
            tenant_offboarding_store=offboarding_store,
            storage_catalog=catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    created = client.post(
        "/api/lifecycle/tenant-offboarding-requests",
        json={
            "reason": "customer requested account closure",
            "export_before_delete": True,
            "categories": ["storage_object"],
        },
        headers=headers,
    )
    approved = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/approve",
        headers=headers,
    )

    exported = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/export-bundles",
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert approved.status_code == 200
    assert approved.json()["state"] == "export_pending"
    assert exported.status_code == 201
    assert exported.json()["state"] == "deletion_pending"
    assert exported.json()["export_bundle_id"]
    assert exported.json()["export_storage_object_id"]
    assert exported.json()["export_completed_at"] is not None
    assert len(storage_client.put_objects) == 1
    uploaded = storage_client.put_objects[0]
    assert "/None/" not in uploaded["Key"]
    content = json.loads(uploaded["Body"].decode("utf-8"))
    exported_resource_ids = {
        item["resource_id"] for item in content["manifest"]["items"]
    }
    assert exported_resource_ids == {first_object.id, second_object.id}
    assert content["manifest"]["workspace_id"] is None
    completed_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.offboarding.export_completed"
    ]
    assert len(completed_events) == 1
    assert completed_events[0]["metadata"]["state"] == "deletion_pending"
    assert completed_events[0]["metadata"]["export_bundle_id"] == exported.json()[
        "export_bundle_id"
    ]
    assert completed_events[0]["metadata"]["export_storage_object_id"] == exported.json()[
        "export_storage_object_id"
    ]
    assert completed_events[0]["metadata"]["item_count"] == 2
    assert "manifest" not in completed_events[0]["metadata"]
    assert "items" not in completed_events[0]["metadata"]
    assert "reason" not in completed_events[0]["metadata"]


def test_tenant_offboarding_api_does_not_upload_export_bundle_before_approval():
    identity, admin = create_offboarding_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
            storage_catalog=catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    created = client.post(
        "/api/lifecycle/tenant-offboarding-requests",
        json={
            "reason": "customer requested account closure",
            "export_before_delete": True,
            "categories": ["storage_object"],
        },
        headers=headers,
    )

    response = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/export-bundles",
        headers=headers,
    )

    assert response.status_code == 409
    assert storage_client.put_objects == []


def test_tenant_offboarding_api_executes_deletion_and_records_summary_audit():
    identity, admin = create_offboarding_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    offboarding_store = InMemoryTenantOffboardingStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    first_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_sales",
            purpose=StoragePurpose.ARTIFACT,
            filename="sales-summary.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    second_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_hr",
            run_id="run_hr",
            purpose=StoragePurpose.ARTIFACT,
            filename="hr-summary.md",
            content_type="text/markdown",
            size_bytes=256,
        )
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
            tenant_offboarding_store=offboarding_store,
            storage_catalog=catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    created = client.post(
        "/api/lifecycle/tenant-offboarding-requests",
        json={
            "reason": "customer requested account closure",
            "export_before_delete": True,
            "categories": ["storage_object"],
        },
        headers=headers,
    )
    client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/approve",
        headers=headers,
    )
    exported = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/export-bundles",
        headers=headers,
    )

    deleted = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/delete",
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert deleted.status_code == 200
    assert deleted.json()["plan"]["state"] == "deleted"
    assert deleted.json()["deleted_count"] == 2
    expected_deleted_ids = [
        storage_object.id
        for storage_object in sorted(
            [first_object, second_object],
            key=lambda storage_object: (storage_object.created_at, storage_object.id),
        )
    ]
    assert deleted.json()["deleted_storage_object_ids"] == expected_deleted_ids
    assert deleted.json()["preserved_storage_object_ids"] == [
        exported.json()["export_storage_object_id"]
    ]
    assert len(storage_client.deleted_objects) == 2
    with pytest.raises(NotFoundError):
        catalog.get("tenant_acme", first_object.id)
    with pytest.raises(NotFoundError):
        catalog.get("tenant_acme", second_object.id)
    assert catalog.get(
        "tenant_acme",
        exported.json()["export_storage_object_id"],
    ).id == exported.json()["export_storage_object_id"]
    deleted_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.offboarding.deleted"
    ]
    assert len(deleted_events) == 1
    assert deleted_events[0]["metadata"]["state"] == "deleted"
    assert deleted_events[0]["metadata"]["deleted_storage_object_count"] == 2
    assert deleted_events[0]["metadata"]["preserved_storage_object_count"] == 1
    assert "reason" not in deleted_events[0]["metadata"]
    assert "deleted_storage_object_ids" not in deleted_events[0]["metadata"]


def test_tenant_offboarding_api_does_not_delete_before_deletion_pending():
    identity, admin = create_offboarding_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_sales",
            purpose=StoragePurpose.ARTIFACT,
            filename="sales-summary.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
            storage_catalog=catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    created = client.post(
        "/api/lifecycle/tenant-offboarding-requests",
        json={
            "reason": "customer requested account closure",
            "export_before_delete": True,
            "categories": ["storage_object"],
        },
        headers=headers,
    )

    response = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/delete",
        headers=headers,
    )

    assert response.status_code == 409
    assert storage_client.deleted_objects == []


def test_tenant_offboarding_api_deletes_memory_and_records_summary_audit():
    identity, admin = create_offboarding_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    long_memory = InMemoryLongTermMemoryService()
    short_memory = InMemoryShortTermMemoryService()
    memory = long_memory.write(
        MemoryWriteRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            scope_type=MemoryScopeType.COMPANY,
            scope_id="tenant_acme",
            source_run_id="run_123",
            content="Customer-specific memory.",
            created_by=admin.id,
            metadata={"source": "conversation"},
        )
    )
    now = utc_now()
    short_memory.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "delete memory"},
        ),
        now=now,
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=RecordingObjectStorageClient(),
            ),
            long_term_memory_service=long_memory,
            short_term_memory_service=short_memory,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    created = client.post(
        "/api/lifecycle/tenant-offboarding-requests",
        json={
            "reason": "customer requested account closure",
            "export_before_delete": False,
            "categories": ["memory"],
        },
        headers=headers,
    )
    client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/approve",
        headers=headers,
    )

    deleted = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/delete",
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert deleted.status_code == 200
    assert deleted.json()["plan"]["state"] == "deleted"
    assert deleted.json()["deleted_memory_record_count"] == 1
    assert deleted.json()["deleted_short_term_memory_count"] == 1
    assert long_memory.get("tenant_acme", memory.id).status == MemoryStatus.EXPIRED
    assert long_memory.get("tenant_acme", memory.id).content == ""
    assert short_memory.list_for_run("tenant_acme", "run_123", now=now) == []
    deleted_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.offboarding.deleted"
    ]
    assert len(deleted_events) == 1
    assert deleted_events[0]["metadata"]["deleted_memory_record_count"] == 1
    assert deleted_events[0]["metadata"]["deleted_short_term_memory_count"] == 1
    assert "deleted_memory_record_ids" not in deleted_events[0]["metadata"]
    assert "reason" not in deleted_events[0]["metadata"]


def test_tenant_offboarding_api_deletes_knowledge_and_records_summary_audit():
    identity, admin = create_offboarding_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    knowledge_service = InMemoryKnowledgeService()
    knowledge_base = knowledge_service.create_base(
        tenant_id="tenant_acme",
        user_id=admin.id,
        request=KnowledgeBaseCreate(workspace_id="workspace_sales", name="Sales Playbook"),
    )
    document = knowledge_service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            knowledge_base_id=knowledge_base.id,
            source_uri="s3://tenant_acme/playbooks/sales.md",
            source_document_id="sales_doc",
            uploaded_by_user_id=admin.id,
            title="Sales Playbook",
            acl_subjects=["team:sales"],
            sensitivity_level=1,
            document_version="v1",
            content_hash="hash_sales_api_offboarding",
            chunks=[DocumentChunkCreate(content="Renewal playbook guidance.")],
        )
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
            knowledge_service=knowledge_service,
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=RecordingObjectStorageClient(),
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    created = client.post(
        "/api/lifecycle/tenant-offboarding-requests",
        json={
            "reason": "customer requested account closure",
            "export_before_delete": False,
            "categories": ["knowledge"],
        },
        headers=headers,
    )
    client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/approve",
        headers=headers,
    )

    deleted = client.post(
        f"/api/lifecycle/tenant-offboarding-requests/{created.json()['id']}/delete",
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert deleted.status_code == 200
    assert deleted.json()["plan"]["state"] == "deleted"
    assert deleted.json()["deleted_knowledge_base_count"] == 1
    assert deleted.json()["deleted_knowledge_document_count"] == 1
    assert deleted.json()["deleted_knowledge_chunk_count"] == 1
    with pytest.raises(NotFoundError):
        knowledge_service.list_chunks("tenant_acme", document.id)
    deleted_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.offboarding.deleted"
    ]
    assert len(deleted_events) == 1
    assert deleted_events[0]["metadata"]["deleted_knowledge_base_count"] == 1
    assert deleted_events[0]["metadata"]["deleted_knowledge_document_count"] == 1
    assert deleted_events[0]["metadata"]["deleted_knowledge_chunk_count"] == 1
    assert "deleted_knowledge_document_ids" not in deleted_events[0]["metadata"]
    assert "reason" not in deleted_events[0]["metadata"]
