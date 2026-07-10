from pydantic import BaseModel, Field

from taroai.knowledge.models import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeDocument,
    KnowledgeDocumentCreate,
    KnowledgeTenantDeletionResult,
    RetrievalRequest,
    RetrievalResult,
)
from taroai.knowledge.retrieval import retrieve_chunks
from taroai.store import NotFoundError


class InMemoryKnowledgeService(BaseModel):
    bases: dict[str, KnowledgeBase] = Field(default_factory=dict)
    documents: dict[str, KnowledgeDocument] = Field(default_factory=dict)
    chunks: dict[str, list[DocumentChunk]] = Field(default_factory=dict)
    content_hashes: set[str] = Field(default_factory=set)

    def create_base(
        self,
        tenant_id: str,
        user_id: str,
        request: KnowledgeBaseCreate,
    ) -> KnowledgeBase:
        knowledge_base = KnowledgeBase(
            tenant_id=tenant_id,
            workspace_id=request.workspace_id,
            name=request.name,
            description=request.description,
            created_by_user_id=user_id,
        )
        self.bases[knowledge_base.id] = knowledge_base
        return knowledge_base

    def list_bases_for_tenant(self, tenant_id: str) -> list[KnowledgeBase]:
        return [
            base
            for base in sorted(self.bases.values(), key=lambda item: (item.created_at, item.id))
            if base.tenant_id == tenant_id
        ]

    def list_bases_for_workspace(self, tenant_id: str, workspace_id: str) -> list[KnowledgeBase]:
        return [
            base
            for base in self.list_bases_for_tenant(tenant_id)
            if base.workspace_id == workspace_id
        ]

    def list_documents(
        self,
        tenant_id: str,
        knowledge_base_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[KnowledgeDocument]:
        return [
            document
            for document in sorted(
                self.documents.values(),
                key=lambda item: (item.created_at, item.id),
            )
            if document.tenant_id == tenant_id
            and (
                knowledge_base_id is None
                or document.knowledge_base_id == knowledge_base_id
            )
            and (workspace_id is None or document.workspace_id == workspace_id)
        ]

    def register_document(self, request: KnowledgeDocumentCreate) -> KnowledgeDocument:
        knowledge_base = self.bases.get(request.knowledge_base_id)
        if knowledge_base is None:
            raise NotFoundError(f"Knowledge base not found: {request.knowledge_base_id}")
        if knowledge_base.tenant_id != request.tenant_id:
            raise NotFoundError(f"Knowledge base not found: {request.knowledge_base_id}")
        if knowledge_base.workspace_id != request.workspace_id:
            raise ValueError("Knowledge document workspace does not match knowledge base workspace")
        if request.content_hash in self.content_hashes:
            raise ValueError(f"Knowledge document content hash already exists: {request.content_hash}")

        document = KnowledgeDocument(
            **request.model_dump(exclude={"chunks"}),
        )
        self.documents[document.id] = document
        self.content_hashes.add(document.content_hash)
        self.chunks[document.id] = [
            DocumentChunk(
                tenant_id=document.tenant_id,
                workspace_id=document.workspace_id,
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                source_document_id=document.source_document_id,
                source_uri=document.source_uri,
                content=chunk.content,
                citation=chunk.citation,
                acl_subjects=document.acl_subjects,
                sensitivity_level=document.sensitivity_level,
                embedding=chunk.embedding,
                embedding_model=chunk.embedding_model,
                embedding_provider=chunk.embedding_provider,
                embedded_at=chunk.embedded_at,
            )
            for chunk in request.chunks
        ]
        return document

    def list_chunks(self, tenant_id: str, document_id: str) -> list[DocumentChunk]:
        document = self.documents.get(document_id)
        if document is None:
            raise NotFoundError(f"Knowledge document not found: {document_id}")
        if document.tenant_id != tenant_id:
            return []
        return list(self.chunks.get(document_id, []))

    def retrieve(self, request: RetrievalRequest) -> list[RetrievalResult]:
        all_chunks = [chunk for document_chunks in self.chunks.values() for chunk in document_chunks]
        return retrieve_chunks(all_chunks, request)

    def delete_for_tenant(self, tenant_id: str) -> KnowledgeTenantDeletionResult:
        deleted_base_ids = [
            base.id
            for base in sorted(self.bases.values(), key=lambda item: (item.created_at, item.id))
            if base.tenant_id == tenant_id
        ]
        deleted_documents = [
            document
            for document in sorted(self.documents.values(), key=lambda item: (item.created_at, item.id))
            if document.tenant_id == tenant_id
        ]
        deleted_document_ids = [document.id for document in deleted_documents]
        deleted_chunk_count = sum(len(self.chunks.get(document_id, [])) for document_id in deleted_document_ids)

        for document in deleted_documents:
            self.content_hashes.discard(document.content_hash)
            self.documents.pop(document.id, None)
            self.chunks.pop(document.id, None)
        for base_id in deleted_base_ids:
            self.bases.pop(base_id, None)

        return KnowledgeTenantDeletionResult(
            deleted_base_ids=deleted_base_ids,
            deleted_document_ids=deleted_document_ids,
            deleted_chunk_count=deleted_chunk_count,
        )
