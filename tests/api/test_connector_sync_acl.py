from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.audit import AuditService
from taroai.config import Settings
from taroai.connectors import (
    ConnectorAclMapping,
    ConnectorAclMappingRule,
    ConnectorAuthMode,
    ConnectorCapability,
    ConnectorDefinitionCreate,
    ConnectorSyncJob,
    ConnectorSyncDocument,
    ConnectorSyncPlanner,
    ConnectorSyncStatus,
    ConnectorStatus,
    ConnectorType,
    InMemoryConnectorRegistry,
    SourceAclPrincipal,
)
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.knowledge import InMemoryKnowledgeService, KnowledgeBaseCreate
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import InMemoryJobQueue, JobStatus, JobType
import taroai.workers as workers_module


def create_connector_sync_identity(can_sync: bool = True):
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="connector-sync@example.com",
            display_name="Connector Sync",
            password="correct horse battery staple",
        )
    )
    permissions = []
    if can_sync:
        permissions.append(Permission(action="connectors.sync", resource="tenant:tenant_acme"))
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_connector_sync",
            name="Connector Sync",
            permissions=permissions,
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_connector_sync")
    return identity, account


def register_sync_connector(registry: InMemoryConnectorRegistry):
    return registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.SAAS,
            display_name="Sales CRM",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.NONE,
            status=ConnectorStatus.ENABLED,
            capabilities=[
                ConnectorCapability(
                    name="sync_accounts",
                    required_scopes=["crm.accounts.read"],
                )
            ],
        )
    )


def test_connector_acl_mapping_preserves_source_access_as_platform_subjects():
    mapping = ConnectorAclMapping(
        rules=[
            ConnectorAclMappingRule(
                source_principal_id="group:sales",
                acl_subject="team:sales",
            ),
            ConnectorAclMappingRule(
                source_principal_id="user:alex@example.com",
                acl_subject="user:user_alex",
            ),
        ]
    )

    subjects = mapping.map_principals(
        [
            SourceAclPrincipal(
                source_principal_id="group:sales",
                principal_type="group",
            ),
            SourceAclPrincipal(
                source_principal_id="user:alex@example.com",
                principal_type="user",
            ),
        ]
    )

    assert subjects == ["team:sales", "user:user_alex"]


def test_connector_sync_document_becomes_knowledge_document_without_memory_write():
    planner = ConnectorSyncPlanner(
        acl_mapping=ConnectorAclMapping(
            rules=[
                ConnectorAclMappingRule(
                    source_principal_id="group:sales",
                    acl_subject="team:sales",
                )
            ]
        )
    )
    document = ConnectorSyncDocument(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        connector_id="connector_crm",
        source_uri="crm://accounts/acme",
        source_document_id="crm_account_123",
        title="Acme Account",
        document_version="v3",
        content_hash="sha256:abc123",
        sensitivity_level=2,
        source_acl=[
            SourceAclPrincipal(
                source_principal_id="group:sales",
                principal_type="group",
            )
        ],
        chunks=[
            {
                "content": "Renewal is in legal review.",
                "citation": {"source": "crm"},
            }
        ],
    )

    plan = planner.plan_knowledge_ingestion(
        document,
        uploaded_by_user_id="svc_connector_sync",
        knowledge_base_id="knowledge_sales",
    )

    assert plan.memory_write_count == 0
    assert plan.knowledge_document.acl_subjects == ["team:sales"]
    assert plan.knowledge_document.source_uri == "crm://accounts/acme"
    assert plan.knowledge_document.source_document_id == "crm_account_123"
    assert plan.knowledge_document.chunks[0].content == "Renewal is in legal review."


def test_connector_sync_job_api_enqueues_worker_job_without_raw_document_audit():
    identity, account = create_connector_sync_identity()
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    registry = InMemoryConnectorRegistry()
    connector = register_sync_connector(registry)
    knowledge_service = InMemoryKnowledgeService()
    knowledge_base = knowledge_service.create_base(
        tenant_id="tenant_acme",
        user_id=account.id,
        request=KnowledgeBaseCreate(
            workspace_id="workspace_sales",
            name="Sales Knowledge",
        ),
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            knowledge_service=knowledge_service,
            job_queue=queue,
            settings=Settings(_env_file=None),
        )
    )

    response = client.post(
        f"/api/connectors/{connector.id}/sync-jobs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "knowledge_base_id": knowledge_base.id,
            "documents": [
                {
                    "tenant_id": "tenant_acme",
                    "workspace_id": "workspace_sales",
                    "connector_id": connector.id,
                    "source_uri": "crm://accounts/acme",
                    "source_document_id": "crm_account_123",
                    "title": "Acme Account",
                    "document_version": "v3",
                    "content_hash": "sha256:connector-sync-acme",
                    "sensitivity_level": 2,
                    "source_acl": [
                        {
                            "source_principal_id": "group:sales",
                            "principal_type": "group",
                        }
                    ],
                    "chunks": [
                        {
                            "content": "Renewal is in legal review.",
                            "citation": {"source": "crm"},
                        }
                    ],
                }
            ],
            "acl_mapping": {
                "rules": [
                    {
                        "source_principal_id": "group:sales",
                        "acl_subject": "team:sales",
                    }
                ]
            },
            "cursor": "cursor_001",
        },
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == queue.jobs[0].id
    assert response.json()["run_id"] == queue.jobs[0].payload["run_id"]
    assert response.json()["queue"] == "connectors.sync"
    assert queue.jobs[0].type == JobType.CONNECTOR_SYNC
    assert queue.jobs[0].payload["connector_id"] == connector.id
    sync_state = registry.get_connector("tenant_acme", connector.id).sync_state
    assert sync_state is not None
    assert sync_state.status == ConnectorSyncStatus.PENDING
    assert sync_state.run_id == response.json()["run_id"]
    assert sync_state.job_id == response.json()["job_id"]
    assert sync_state.knowledge_base_id == knowledge_base.id
    assert sync_state.cursor == "cursor_001"

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.sync_requested"
    ]
    assert len(events) == 1
    assert events[0].metadata["connector_id"] == connector.id
    assert events[0].metadata["document_count"] == 1
    assert events[0].metadata["chunk_count"] == 1
    assert "Renewal is in legal review" not in str(events[0].metadata)


def test_connector_sync_worker_registers_knowledge_documents_and_billing():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    registry = InMemoryConnectorRegistry()
    connector = register_sync_connector(registry)
    knowledge_service = InMemoryKnowledgeService()
    knowledge_base = knowledge_service.create_base(
        tenant_id="tenant_acme",
        user_id="svc_connector_sync",
        request=KnowledgeBaseCreate(
            workspace_id="workspace_sales",
            name="Sales Knowledge",
        ),
    )
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="svc_connector_sync",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="connector_sync",
            message=f"Sync connector {connector.id} into knowledge base.",
            mode=RunMode.AUTONOMOUS,
        ),
    )
    queued_job = queue.enqueue(
        JobType.CONNECTOR_SYNC,
        ConnectorSyncJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            connector_id=connector.id,
            run_id=run.id,
            knowledge_base_id=knowledge_base.id,
            requested_by_user_id="svc_connector_sync",
            cursor="cursor_001",
            acl_mapping=ConnectorAclMapping(
                rules=[
                    ConnectorAclMappingRule(
                        source_principal_id="group:sales",
                        acl_subject="team:sales",
                    )
                ]
            ),
            documents=[
                ConnectorSyncDocument(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    connector_id=connector.id,
                    source_uri="crm://accounts/acme",
                    source_document_id="crm_account_123",
                    title="Acme Account",
                    document_version="v3",
                    content_hash="sha256:connector-sync-worker-acme",
                    sensitivity_level=2,
                    source_acl=[
                        SourceAclPrincipal(
                            source_principal_id="group:sales",
                            principal_type="group",
                        )
                    ],
                    chunks=[
                        {
                            "content": "Renewal is in legal review.",
                            "citation": {"source": "crm"},
                        }
                    ],
                )
            ],
        ),
    )
    worker = workers_module.ConnectorSyncWorker(
        queue=queue,
        knowledge_service=knowledge_service,
        store=store,
        connector_registry=registry,
        audit_service=AuditService(store=store),
    )

    processed = worker.process_next()

    assert processed is not None
    assert processed.id == queued_job.id
    assert processed.status == JobStatus.SUCCEEDED
    documents = knowledge_service.list_documents("tenant_acme", knowledge_base.id)
    assert len(documents) == 1
    assert documents[0].acl_subjects == ["team:sales"]
    assert documents[0].source_uri == "crm://accounts/acme"
    chunks = knowledge_service.list_chunks("tenant_acme", documents[0].id)
    assert chunks[0].content == "Renewal is in legal review."

    meters = [
        meter
        for meter in store.list_billing_meters("tenant_acme")
        if meter.meter_type == "connector_sync_document_count"
    ]
    assert len(meters) == 1
    assert meters[0].quantity == 1
    assert meters[0].metadata["connector_id"] == connector.id
    sync_state = registry.get_connector("tenant_acme", connector.id).sync_state
    assert sync_state is not None
    assert sync_state.status == ConnectorSyncStatus.SUCCEEDED
    assert sync_state.run_id == run.id
    assert sync_state.job_id == queued_job.id
    assert sync_state.knowledge_base_id == knowledge_base.id
    assert sync_state.cursor == "cursor_001"
    assert sync_state.completed_at is not None

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.sync_completed"
    ]
    assert len(events) == 1
    assert events[0].metadata["document_count"] == 1
    assert events[0].metadata["chunk_count"] == 1
    assert "Renewal is in legal review" not in str(events[0].metadata)


def test_connector_sync_worker_persists_failed_sync_state_without_raw_content():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    registry = InMemoryConnectorRegistry()
    connector = register_sync_connector(registry)
    knowledge_service = InMemoryKnowledgeService()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="svc_connector_sync",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="connector_sync",
            message=f"Sync connector {connector.id} into knowledge base.",
            mode=RunMode.AUTONOMOUS,
        ),
    )
    queued_job = queue.enqueue(
        JobType.CONNECTOR_SYNC,
        ConnectorSyncJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            connector_id=connector.id,
            run_id=run.id,
            knowledge_base_id="knowledge_missing",
            requested_by_user_id="svc_connector_sync",
            cursor="cursor_failed",
            documents=[
                ConnectorSyncDocument(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    connector_id=connector.id,
                    source_uri="crm://accounts/acme",
                    source_document_id="crm_account_123",
                    title="Acme Account",
                    document_version="v3",
                    content_hash="sha256:connector-sync-worker-failed",
                    chunks=[
                        {
                            "content": "Do not store this raw failed document text.",
                            "citation": {"source": "crm"},
                        }
                    ],
                )
            ],
        ),
        max_attempts=1,
    )
    worker = workers_module.ConnectorSyncWorker(
        queue=queue,
        knowledge_service=knowledge_service,
        store=store,
        connector_registry=registry,
        audit_service=AuditService(store=store),
    )

    processed = worker.process_next()

    assert processed is not None
    assert processed.id == queued_job.id
    assert processed.status == JobStatus.DEAD_LETTER
    sync_state = registry.get_connector("tenant_acme", connector.id).sync_state
    assert sync_state is not None
    assert sync_state.status == ConnectorSyncStatus.FAILED
    assert sync_state.run_id == run.id
    assert sync_state.job_id == queued_job.id
    assert sync_state.knowledge_base_id == "knowledge_missing"
    assert sync_state.cursor == "cursor_failed"
    assert sync_state.error_code == "NotFoundError"
    assert sync_state.completed_at is not None
    assert "Do not store this raw failed document text" not in str(sync_state.model_dump(mode="json"))
from taroai.domain import RunCreate, RunMode
