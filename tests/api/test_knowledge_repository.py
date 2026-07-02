from pathlib import Path

import pytest

from taroai.db import DatabaseConfig, MigrationRunner
from taroai.knowledge import (
    DocumentChunkCreate,
    KnowledgeBaseCreate,
    KnowledgeDocumentCreate,
    RetrievalRequest,
)
from taroai.store import NotFoundError


def test_sql_knowledge_service_persists_documents_chunks_and_retrieves_by_acl(tmp_path: Path):
    from taroai.knowledge.repository import SqlKnowledgeService

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    service = SqlKnowledgeService(config=DatabaseConfig(url=database_url))
    knowledge_base = service.create_base(
        tenant_id="tenant_acme",
        user_id="user_1",
        request=KnowledgeBaseCreate(
            workspace_id="workspace_sales",
            name="Sales Playbook",
        ),
    )
    document = service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            knowledge_base_id=knowledge_base.id,
            source_uri="s3://tenant_acme/playbooks/renewal.md",
            source_document_id="doc_renewal",
            uploaded_by_user_id="user_1",
            title="Renewal Playbook",
            acl_subjects=["user_1"],
            sensitivity_level=1,
            document_version="1.0.0",
            content_hash="hash_renewal_sql",
            chunks=[
                DocumentChunkCreate(
                    content="Renewal playbook requires QBR and security review.",
                    citation={"page": 3},
                )
            ],
        )
    )

    restarted = SqlKnowledgeService(config=DatabaseConfig(url=database_url))
    results = restarted.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="renewal security",
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["user_1"],
            clearance_level=1,
        )
    )
    denied_results = restarted.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="renewal security",
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["user_2"],
            clearance_level=1,
        )
    )

    assert [chunk.id for chunk in restarted.list_chunks("tenant_acme", document.id)] == [
        results[0].chunk_id
    ]
    assert results[0].document_id == document.id
    assert results[0].source_document_id == "doc_renewal"
    assert results[0].citation == {"page": 3}
    assert denied_results == []
    assert restarted.list_chunks("tenant_other", document.id) == []


def test_sql_knowledge_service_delete_for_tenant_removes_bases_documents_and_chunks(
    tmp_path: Path,
):
    from taroai.knowledge.repository import SqlKnowledgeService

    database_url = f"sqlite:///{tmp_path / 'taroai-delete-knowledge.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    service = SqlKnowledgeService(config=DatabaseConfig(url=database_url))
    knowledge_base = service.create_base(
        tenant_id="tenant_acme",
        user_id="user_1",
        request=KnowledgeBaseCreate(
            workspace_id="workspace_sales",
            name="Sales Playbook",
        ),
    )
    document = service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            knowledge_base_id=knowledge_base.id,
            source_uri="s3://tenant_acme/playbooks/renewal.md",
            source_document_id="doc_renewal",
            uploaded_by_user_id="user_1",
            title="Renewal Playbook",
            acl_subjects=["user_1"],
            sensitivity_level=1,
            document_version="1.0.0",
            content_hash="hash_renewal_delete_sql",
            chunks=[DocumentChunkCreate(content="Renewal playbook guidance.")],
        )
    )
    other_base = service.create_base(
        tenant_id="tenant_other",
        user_id="user_2",
        request=KnowledgeBaseCreate(
            workspace_id="workspace_other",
            name="Other Playbook",
        ),
    )
    other_document = service.register_document(
        KnowledgeDocumentCreate(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            knowledge_base_id=other_base.id,
            source_uri="s3://tenant_other/playbooks/other.md",
            source_document_id="doc_other",
            uploaded_by_user_id="user_2",
            title="Other Playbook",
            acl_subjects=["user_2"],
            sensitivity_level=0,
            document_version="1.0.0",
            content_hash="hash_other_delete_sql",
            chunks=[DocumentChunkCreate(content="Other tenant guidance.")],
        )
    )

    result = service.delete_for_tenant("tenant_acme")
    restarted = SqlKnowledgeService(config=DatabaseConfig(url=database_url))

    assert result.deleted_base_ids == [knowledge_base.id]
    assert result.deleted_document_ids == [document.id]
    assert result.deleted_chunk_count == 1
    with pytest.raises(NotFoundError):
        restarted.list_chunks("tenant_acme", document.id)
    assert restarted.retrieve(
        RetrievalRequest(
            tenant_id="tenant_acme",
            query="renewal",
            allowed_workspace_ids=["workspace_sales"],
            acl_subjects=["user_1"],
            clearance_level=1,
        )
    ) == []
    assert [chunk.id for chunk in restarted.list_chunks("tenant_other", other_document.id)]
