from fastapi.testclient import TestClient
from pydantic import Field
import pytest

from taroai.app import create_app
from taroai.billing import BillingPricingRule
from taroai.config import Settings
from taroai.embeddings import (
    EmbeddingGateway,
    EmbeddingGatewayRequest,
    EmbeddingGatewayResponse,
    EmbeddingUsage,
)
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.knowledge import (
    DocumentChunkCreate,
    InMemoryKnowledgeService,
    KnowledgeBaseCreate,
    KnowledgeDocumentCreate,
    RetrievalRequest,
)
from taroai.storage import InMemoryStorageCatalog, S3CompatibleObjectStorage
from taroai.store import InMemoryControlPlaneStore, NotFoundError


class RecordingKnowledgeEmbeddingGateway(EmbeddingGateway):
    requests: list[EmbeddingGatewayRequest] = Field(default_factory=list, exclude=True, repr=False)

    def embed(self, request: EmbeddingGatewayRequest) -> EmbeddingGatewayResponse:
        self.requests.append(request)
        embeddings = []
        for index, text in enumerate(request.input):
            normalized = text.lower()
            if request.purpose == "knowledge_query" or "procurement" in normalized:
                vector = [0.0, 1.0]
            else:
                vector = [1.0, 0.0]
            embeddings.append({"index": index, "embedding": vector})
        return EmbeddingGatewayResponse(
            model=request.model or "text-embedding-3-small",
            embeddings=embeddings,
            usage=EmbeddingUsage(
                prompt_tokens=len(request.input) * 7,
                total_tokens=len(request.input) * 7,
            ),
        )


class RecordingKnowledgeStorageClient:
    def __init__(self):
        self.put_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {"ETag": '"etag_from_knowledge_upload"'}

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Body": RecordingKnowledgeStorageBody(self.objects[(kwargs["Bucket"], kwargs["Key"])])}


class RecordingKnowledgeStorageBody:
    def __init__(self, content: bytes):
        self.content = content

    def read(self) -> bytes:
        return self.content


def create_knowledge_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    curator = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="knowledge-curator@example.com",
            display_name="Knowledge Curator",
            password="correct horse battery staple",
        )
    )
    reader = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="knowledge-reader@example.com",
            display_name="Knowledge Reader",
            password="correct horse battery staple",
        )
    )
    no_access = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="knowledge-no-access@example.com",
            display_name="No Access",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_knowledge_curator",
            name="Knowledge Curator",
            permissions=[
                Permission(action="knowledge.read", resource="tenant:tenant_acme"),
                Permission(action="knowledge.write", resource="tenant:tenant_acme"),
                Permission(action="storage.read", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
                Permission(action="billing.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_knowledge_reader",
            name="Knowledge Reader",
            permissions=[
                Permission(action="knowledge.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", curator.id, "role_knowledge_curator")
    identity.assign_role("tenant_acme", reader.id, "role_knowledge_reader")
    return identity, curator, reader, no_access


def test_knowledge_service_registers_document_chunks_with_source_metadata():
    service = InMemoryKnowledgeService()
    knowledge_base = service.create_base(
        tenant_id="tenant_acme",
        user_id="user_1",
        request=KnowledgeBaseCreate(
            workspace_id="workspace_sales",
            name="Sales Playbook",
            description="Approved sales guidance.",
        ),
    )

    document = service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            knowledge_base_id=knowledge_base.id,
            source_uri="s3://tenant_acme/playbook.md",
            source_document_id="doc_sales_playbook",
            uploaded_by_user_id="user_1",
            title="Sales Playbook",
            acl_subjects=["team:sales", "user:user_1"],
            sensitivity_level=1,
            document_version="2026.07",
            content_hash="sha256:playbook",
            chunks=[
                DocumentChunkCreate(
                    content="Discovery calls should qualify budget, authority, need, and timing.",
                    citation={"page": 3},
                )
            ],
        )
    )

    chunks = service.list_chunks("tenant_acme", document.id)

    assert document.knowledge_base_id == knowledge_base.id
    assert document.acl_subjects == ["team:sales", "user:user_1"]
    assert chunks[0].document_id == document.id
    assert chunks[0].content.startswith("Discovery calls")
    assert chunks[0].citation == {"page": 3}


def test_knowledge_retrieval_filters_by_tenant_workspace_acl_and_sensitivity():
    service = InMemoryKnowledgeService()
    sales_base = service.create_base(
        tenant_id="tenant_acme",
        user_id="user_1",
        request=KnowledgeBaseCreate(workspace_id="workspace_sales", name="Sales Playbook"),
    )
    finance_base = service.create_base(
        tenant_id="tenant_acme",
        user_id="user_2",
        request=KnowledgeBaseCreate(workspace_id="workspace_finance", name="Finance Playbook"),
    )
    service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            knowledge_base_id=sales_base.id,
            source_uri="s3://tenant_acme/sales.md",
            source_document_id="sales_doc",
            uploaded_by_user_id="user_1",
            title="Sales Guidance",
            acl_subjects=["team:sales"],
            sensitivity_level=1,
            document_version="v1",
            content_hash="sha256:sales",
            chunks=[
                DocumentChunkCreate(
                    content="Enterprise discovery requires compliance review before renewal pricing.",
                    citation={"section": "discovery"},
                )
            ],
        )
    )
    service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_finance",
            knowledge_base_id=finance_base.id,
            source_uri="s3://tenant_acme/finance.md",
            source_document_id="finance_doc",
            uploaded_by_user_id="user_2",
            title="Finance Guidance",
            acl_subjects=["team:finance"],
            sensitivity_level=3,
            document_version="v1",
            content_hash="sha256:finance",
            chunks=[
                DocumentChunkCreate(
                    content="Renewal pricing requires finance approval.",
                    citation={"section": "finance"},
                )
            ],
        )
    )

    allowed_results = service.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="renewal pricing compliance",
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["team:sales"],
            clearance_level=1,
        )
    )
    blocked_results = service.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="renewal pricing",
            allowed_workspace_ids=["workspace_sales", "workspace_finance"],
            acl_subjects=["team:sales"],
            clearance_level=1,
        )
    )
    other_tenant_results = service.retrieve(
        RetrievalRequest(
            tenant_id="tenant_other",
            query="renewal pricing compliance",
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["team:sales"],
            clearance_level=5,
        )
    )

    assert [result.source_document_id for result in allowed_results] == ["sales_doc"]
    assert allowed_results[0].citation == {"section": "discovery"}
    assert allowed_results[0].sensitivity_level == 1
    assert [result.source_document_id for result in blocked_results] == ["sales_doc"]
    assert other_tenant_results == []


def test_knowledge_retrieval_uses_chunk_embeddings_after_acl_filtering():
    service = InMemoryKnowledgeService()
    knowledge_base = service.create_base(
        tenant_id="tenant_acme",
        user_id="user_1",
        request=KnowledgeBaseCreate(workspace_id="workspace_sales", name="Sales Playbook"),
    )
    service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            knowledge_base_id=knowledge_base.id,
            source_uri="s3://tenant_acme/sales.md",
            source_document_id="sales_doc",
            uploaded_by_user_id="user_1",
            title="Sales Guidance",
            acl_subjects=["team:sales"],
            sensitivity_level=1,
            document_version="v1",
            content_hash="sha256:vector-sales",
            chunks=[
                DocumentChunkCreate(
                    content="General account overview.",
                    embedding=[1.0, 0.0],
                    embedding_model="text-embedding-3-small",
                    embedding_provider="openai_compatible",
                ),
                DocumentChunkCreate(
                    content="Procurement approval guidance.",
                    embedding=[0.0, 1.0],
                    embedding_model="text-embedding-3-small",
                    embedding_provider="openai_compatible",
                ),
            ],
        )
    )

    allowed_results = service.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="executive escalation",
            query_embedding=[0.0, 1.0],
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["team:sales"],
            clearance_level=1,
        )
    )
    denied_results = service.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="executive escalation",
            query_embedding=[0.0, 1.0],
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["team:finance"],
            clearance_level=1,
        )
    )

    assert [result.excerpt for result in allowed_results] == ["Procurement approval guidance."]
    assert allowed_results[0].score == 1.0
    assert denied_results == []


def test_knowledge_document_workspace_must_match_knowledge_base_workspace():
    service = InMemoryKnowledgeService()
    knowledge_base = service.create_base(
        tenant_id="tenant_acme",
        user_id="user_1",
        request=KnowledgeBaseCreate(workspace_id="workspace_sales", name="Sales Playbook"),
    )

    with pytest.raises(ValueError, match="workspace does not match knowledge base"):
        service.register_document(
            KnowledgeDocumentCreate(
                tenant_id="tenant_acme",
                workspace_id="workspace_finance",
                knowledge_base_id=knowledge_base.id,
                source_uri="s3://tenant_acme/finance.md",
                source_document_id="finance_doc",
                uploaded_by_user_id="user_2",
                title="Finance Guidance",
                acl_subjects=["team:finance"],
                sensitivity_level=1,
                document_version="v1",
                content_hash="sha256:finance",
                chunks=[DocumentChunkCreate(content="Finance-only renewal policy.")],
            )
        )


def test_knowledge_service_delete_for_tenant_removes_bases_documents_and_chunks():
    service = InMemoryKnowledgeService()
    knowledge_base = service.create_base(
        tenant_id="tenant_acme",
        user_id="user_1",
        request=KnowledgeBaseCreate(workspace_id="workspace_sales", name="Sales Playbook"),
    )
    document = service.register_document(
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
            content_hash="hash_sales_delete",
            chunks=[DocumentChunkCreate(content="Renewal playbook guidance.")],
        )
    )
    other_base = service.create_base(
        tenant_id="tenant_other",
        user_id="user_2",
        request=KnowledgeBaseCreate(workspace_id="workspace_other", name="Other Playbook"),
    )
    other_document = service.register_document(
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
            content_hash="hash_other_delete",
            chunks=[DocumentChunkCreate(content="Other tenant guidance.")],
        )
    )

    result = service.delete_for_tenant("tenant_acme")

    assert result.deleted_base_ids == [knowledge_base.id]
    assert result.deleted_document_ids == [document.id]
    assert result.deleted_chunk_count == 1
    with pytest.raises(NotFoundError):
        service.list_chunks("tenant_acme", document.id)
    assert service.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="renewal",
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["team:sales"],
            clearance_level=1,
        )
    ) == []
    assert [chunk.id for chunk in service.list_chunks("tenant_other", other_document.id)]
    assert service.retrieve(
        RetrievalRequest(
            tenant_id="tenant_other",
            query="other",
            allowed_workspace_ids=["workspace_other"],
            acl_subjects=["team:other"],
            clearance_level=0,
        )
    )


def test_knowledge_api_enforces_permissions_and_records_safe_audit():
    identity, curator, reader, no_access = create_knowledge_identity()
    store = InMemoryControlPlaneStore()
    storage_client = RecordingKnowledgeStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="https://storage.example.com",
                region="us-east-1",
                access_key_id="access",
                secret_access_key="secret",
                client=storage_client,
            ),
        )
    )
    curator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": curator.id}
    curator_storage_headers = {
        **curator_headers,
        "X-ACL-Subjects": "team:sales",
        "X-Clearance-Level": "1",
    }
    reader_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id}
    no_access_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": no_access.id}
    document_content = (
        "# Sales Guidance\n\n"
        "Enterprise discovery requires compliance review before renewal pricing."
    )

    forbidden_base = client.post(
        "/api/knowledge-bases",
        headers=reader_headers,
        json={
            "workspace_id": "workspace_sales",
            "name": "Sales Playbook",
            "description": "Approved sales guidance.",
        },
    )
    base = client.post(
        "/api/knowledge-bases",
        headers=curator_headers,
        json={
            "workspace_id": "workspace_sales",
            "name": "Sales Playbook",
            "description": "Approved sales guidance.",
        },
    )

    assert base.status_code == 201

    document = client.post(
        "/api/knowledge-documents",
        headers=curator_headers,
        json={
            "workspace_id": "workspace_sales",
            "knowledge_base_id": base.json()["id"],
            "source_uri": "s3://tenant_acme/sales.md",
            "source_document_id": "sales_doc",
            "title": "Sales Guidance",
            "content": document_content,
            "content_type": "text/markdown",
            "acl_subjects": ["team:sales"],
            "sensitivity_level": 1,
            "document_version": "v1",
            "content_hash": "sha256:sales",
            "chunks": [
                {
                    "content": "Enterprise discovery requires compliance review before renewal pricing.",
                    "citation": {"section": "discovery"},
                }
            ],
        },
    )
    storage_object_id = document.json()["storage_object_id"]
    downloaded_document = client.get(
        f"/api/storage/objects/{storage_object_id}/content",
        headers=curator_storage_headers,
    )
    forbidden_query = client.post(
        "/api/knowledge/query",
        headers=no_access_headers,
        json={
            "query": "renewal pricing compliance",
            "allowed_workspace_ids": ["workspace_sales"],
            "acl_subjects": ["team:sales"],
            "clearance_level": 1,
        },
    )
    query = client.post(
        "/api/knowledge/query",
        headers=curator_headers,
        json={
            "query": "renewal pricing compliance",
            "allowed_workspace_ids": ["workspace_sales"],
            "acl_subjects": ["team:sales"],
            "clearance_level": 1,
        },
    )
    other_tenant_query = client.post(
        "/api/knowledge/query",
        headers={"X-Tenant-ID": "tenant_other", "X-User-ID": curator.id},
        json={
            "query": "renewal pricing compliance",
            "allowed_workspace_ids": ["workspace_sales"],
            "acl_subjects": ["team:sales"],
            "clearance_level": 5,
        },
    )
    audits = client.get("/api/audit-events", headers=curator_headers)

    assert forbidden_base.status_code == 403
    assert document.status_code == 201
    assert storage_object_id.startswith("storage_")
    assert downloaded_document.status_code == 200
    assert downloaded_document.content == document_content.encode("utf-8")
    assert downloaded_document.headers["content-type"].startswith("text/markdown")
    assert storage_client.put_calls[0]["Bucket"] == "taroai-artifacts"
    assert storage_client.put_calls[0]["Key"].endswith(
        "/knowledge-documents/sales.md"
    )
    assert storage_client.put_calls[0]["Body"] == document_content.encode("utf-8")
    assert storage_client.put_calls[0]["ContentType"] == "text/markdown"
    assert forbidden_query.status_code == 403
    assert query.status_code == 200
    assert [result["source_document_id"] for result in query.json()] == ["sales_doc"]
    assert other_tenant_query.status_code == 403

    knowledge_events = [
        event
        for event in audits.json()
        if event["event_type"]
        in {
            "knowledge.base.created",
            "knowledge.document.registered",
            "knowledge.query.executed",
        }
    ]
    assert [event["event_type"] for event in knowledge_events] == [
        "knowledge.base.created",
        "knowledge.document.registered",
        "knowledge.query.executed",
    ]
    assert knowledge_events[0]["metadata"]["knowledge_base_id"] == base.json()["id"]
    assert knowledge_events[1]["metadata"]["document_id"] == document.json()["id"]
    assert knowledge_events[1]["metadata"]["storage_object_id"] == storage_object_id
    assert knowledge_events[1]["metadata"]["chunk_count"] == 1
    assert knowledge_events[2]["metadata"]["result_count"] == 1
    assert knowledge_events[2]["metadata"]["query_length"] == len("renewal pricing compliance")
    assert document_content not in str(knowledge_events)
    assert "renewal pricing compliance" not in str(knowledge_events)
    assert "Enterprise discovery requires compliance review" not in str(knowledge_events)


def test_knowledge_api_chunks_uploaded_content_when_chunks_are_omitted():
    identity, curator, reader, _ = create_knowledge_identity()
    store = InMemoryControlPlaneStore()
    knowledge_service = InMemoryKnowledgeService()
    storage_catalog = InMemoryStorageCatalog(bucket="knowledge-test")
    storage_client = RecordingKnowledgeStorageClient()
    object_storage = S3CompatibleObjectStorage(
        endpoint_url="https://storage.example.com",
        region="us-east-1",
        access_key_id="access",
        secret_access_key="secret",
        bucket="knowledge-test",
        client=storage_client,
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            knowledge_service=knowledge_service,
            storage_catalog=storage_catalog,
            object_storage=object_storage,
            settings=Settings(
                knowledge_chunk_max_characters=80,
                knowledge_chunk_overlap_characters=10,
                _env_file=None,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": curator.id}
    document_content = (
        "Enterprise onboarding requires kickoff notes, security owners, and success metrics.\n\n"
        "Renewal planning requires procurement context, executive sponsor updates, and risk notes."
    )
    base = client.post(
        "/api/knowledge-bases",
        headers=headers,
        json={"workspace_id": "workspace_sales", "name": "Sales Playbook"},
    ).json()

    document = client.post(
        "/api/knowledge-documents",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "knowledge_base_id": base["id"],
            "source_uri": "s3://tenant_acme/playbooks/onboarding.md",
            "source_document_id": "doc_onboarding",
            "title": "Onboarding Playbook",
            "content": document_content,
            "content_type": "text/markdown",
            "acl_subjects": [reader.id],
            "sensitivity_level": 1,
            "document_version": "1.0.0",
            "content_hash": "sha256:onboarding",
        },
    )
    query = client.post(
        "/api/knowledge/query",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id},
        json={
            "query": "procurement risk",
            "allowed_workspace_ids": ["workspace_sales"],
            "acl_subjects": [reader.id],
            "clearance_level": 1,
            "limit": 1,
        },
    )

    chunks = knowledge_service.list_chunks("tenant_acme", document.json()["id"])
    knowledge_events = [
        event
        for event in client.get("/api/audit-events", headers=headers).json()
        if event["event_type"] == "knowledge.document.registered"
    ]

    assert document.status_code == 201
    assert query.status_code == 200
    assert len(chunks) >= 2
    assert all(chunk.acl_subjects == [reader.id] for chunk in chunks)
    assert all(chunk.sensitivity_level == 1 for chunk in chunks)
    assert all(chunk.citation["source_document_id"] == "doc_onboarding" for chunk in chunks)
    assert all("chunk_index" in chunk.citation for chunk in chunks)
    assert [result["source_document_id"] for result in query.json()] == ["doc_onboarding"]
    assert query.json()[0]["sensitivity_level"] == 1
    assert knowledge_events[0]["metadata"]["chunk_count"] == len(chunks)
    assert document_content not in str(knowledge_events)


def test_knowledge_api_embeds_uploaded_content_and_query_when_gateway_is_configured():
    identity, curator, reader, _ = create_knowledge_identity()
    store = InMemoryControlPlaneStore()
    knowledge_service = InMemoryKnowledgeService()
    storage_client = RecordingKnowledgeStorageClient()
    embedding_gateway = RecordingKnowledgeEmbeddingGateway()
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            knowledge_service=knowledge_service,
            storage_catalog=InMemoryStorageCatalog(bucket="knowledge-test"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="https://storage.example.com",
                region="us-east-1",
                access_key_id="access",
                secret_access_key="secret",
                bucket="knowledge-test",
                client=storage_client,
            ),
            embedding_gateway=embedding_gateway,
            settings=Settings(
                knowledge_chunk_max_characters=80,
                knowledge_chunk_overlap_characters=10,
                embedding_gateway_model="text-embedding-3-small",
                billing_pricing_rules=[
                    BillingPricingRule(
                        meter_type="embedding_tokens",
                        unit="token",
                        provider="openai_compatible",
                        model="text-embedding-3-small",
                        price_per_unit=0.002,
                        pricing_unit_quantity=1000,
                    )
                ],
                _env_file=None,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": curator.id}
    document_content = (
        "General onboarding notes for account teams.\n\n"
        "Procurement approval guidance and renewal risk notes."
    )
    base = client.post(
        "/api/knowledge-bases",
        headers=headers,
        json={"workspace_id": "workspace_sales", "name": "Sales Playbook"},
    ).json()

    document = client.post(
        "/api/knowledge-documents",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "knowledge_base_id": base["id"],
            "source_uri": "s3://tenant_acme/playbooks/renewal.md",
            "source_document_id": "doc_renewal",
            "title": "Renewal Playbook",
            "content": document_content,
            "acl_subjects": [reader.id],
            "sensitivity_level": 1,
            "document_version": "1.0.0",
            "content_hash": "sha256:renewal-embedding",
        },
    )
    query = client.post(
        "/api/knowledge/query",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id},
        json={
            "query": "executive escalation",
            "allowed_workspace_ids": ["workspace_sales"],
            "acl_subjects": [reader.id],
            "clearance_level": 1,
            "limit": 1,
        },
    )

    chunks = knowledge_service.list_chunks("tenant_acme", document.json()["id"])
    purposes = [request.purpose for request in embedding_gateway.requests]
    embedding_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "embedding.gateway.called"
    ]
    embedding_meters = [
        meter
        for meter in store.list_billing_meters("tenant_acme")
        if meter.meter_type.startswith("embedding")
    ]
    token_meters = client.get(
        "/api/billing/meters",
        headers=headers,
        params={"meter_type": "embedding_tokens"},
    )

    assert document.status_code == 201
    assert query.status_code == 200
    assert purposes == ["knowledge_index", "knowledge_query"]
    assert all(chunk.embedding for chunk in chunks)
    assert all(chunk.embedding_model == "text-embedding-3-small" for chunk in chunks)
    assert [result["source_document_id"] for result in query.json()] == ["doc_renewal"]
    assert "Procurement approval guidance" in query.json()[0]["excerpt"]
    assert [event.metadata["purpose"] for event in embedding_audits] == [
        "knowledge_index",
        "knowledge_query",
    ]
    assert embedding_audits[0].metadata["input_count"] == len(chunks)
    assert embedding_audits[0].metadata["source_document_id"] == "doc_renewal"
    assert embedding_audits[1].metadata["input_count"] == 1
    assert embedding_audits[1].metadata["usage"] == {
        "prompt_tokens": 7,
        "total_tokens": 7,
    }
    assert [(meter.meter_type, meter.quantity, meter.unit) for meter in embedding_meters] == [
        ("embedding_call_count", 1, "call"),
        ("embedding_tokens", len(chunks) * 7, "token"),
        ("embedding_call_count", 1, "call"),
        ("embedding_tokens", 7, "token"),
    ]
    assert {meter.run_id for meter in embedding_meters} == {None}
    assert {meter.workspace_id for meter in embedding_meters} == {"workspace_sales"}
    assert token_meters.status_code == 200
    assert [meter["quantity"] for meter in token_meters.json()] == [len(chunks) * 7, 7]
    assert [meter["cost_estimate"] for meter in token_meters.json()] == [
        round(len(chunks) * 7 * 0.002 / 1000, 10),
        0.000014,
    ]
    assert document_content not in str(embedding_audits)
    assert "executive escalation" not in str(embedding_audits)
    assert "[0.0, 1.0]" not in str(embedding_audits)
    assert document_content not in str(embedding_meters)
    assert "executive escalation" not in str(embedding_meters)
    assert "[0.0, 1.0]" not in str(embedding_meters)


def test_knowledge_api_rejects_document_without_content_or_chunks():
    identity, curator, _, _ = create_knowledge_identity()
    store = InMemoryControlPlaneStore()
    storage_client = RecordingKnowledgeStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            storage_catalog=InMemoryStorageCatalog(bucket="knowledge-test"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="https://storage.example.com",
                region="us-east-1",
                access_key_id="access",
                secret_access_key="secret",
                bucket="knowledge-test",
                client=storage_client,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": curator.id}
    base = client.post(
        "/api/knowledge-bases",
        headers=headers,
        json={"workspace_id": "workspace_sales", "name": "Sales Playbook"},
    ).json()

    document = client.post(
        "/api/knowledge-documents",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "knowledge_base_id": base["id"],
            "source_uri": "s3://tenant_acme/playbooks/empty.md",
            "source_document_id": "doc_empty",
            "title": "Empty Playbook",
            "acl_subjects": [curator.id],
            "document_version": "1.0.0",
            "content_hash": "sha256:empty",
        },
    )

    assert document.status_code == 422
    assert storage_client.put_calls == []


def test_knowledge_document_registration_rejects_source_content_that_matches_scan_policy():
    identity, curator, _, _ = create_knowledge_identity()
    store = InMemoryControlPlaneStore()
    storage_client = RecordingKnowledgeStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="https://storage.example.com",
                region="us-east-1",
                access_key_id="access",
                secret_access_key="secret",
                client=storage_client,
            ),
            settings=Settings(
                object_storage_content_scan_blocked_terms=["customer-secret"],
                _env_file=None,
            ),
        )
    )
    curator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": curator.id}
    base = client.post(
        "/api/knowledge-bases",
        headers=curator_headers,
        json={
            "workspace_id": "workspace_sales",
            "name": "Sales Playbook",
        },
    )

    document = client.post(
        "/api/knowledge-documents",
        headers=curator_headers,
        json={
            "workspace_id": "workspace_sales",
            "knowledge_base_id": base.json()["id"],
            "source_uri": "s3://tenant_acme/sales.md",
            "source_document_id": "sales_doc",
            "title": "Sales Guidance",
            "content": "customer-secret",
            "content_type": "text/markdown",
            "acl_subjects": ["team:sales"],
            "sensitivity_level": 1,
            "document_version": "v1",
            "content_hash": "sha256:sales-secret",
            "chunks": [
                {
                    "content": "Approved sales guidance.",
                    "citation": {"section": "overview"},
                }
            ],
        },
    )
    audits = client.get("/api/audit-events", headers=curator_headers)

    assert document.status_code == 422
    assert document.json()["code"] == "storage_content_rejected"
    assert storage_client.put_calls == []
    rejected_events = [
        event
        for event in audits.json()
        if event["event_type"] == "storage.content_rejected"
    ]
    assert rejected_events[0]["metadata"]["matched_term_count"] == 1
    assert "customer-secret" not in str(rejected_events)
