from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from taroai.domain import new_id, utc_now


class KnowledgeBaseCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None


class KnowledgeBase(BaseModel):
    id: str = Field(default_factory=lambda: new_id("knowledge_base"))
    tenant_id: str
    workspace_id: str
    name: str
    description: str | None = None
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)


class DocumentChunkCreate(BaseModel):
    content: str = Field(min_length=1)
    citation: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] = Field(default_factory=list)
    embedding_model: str | None = None
    embedding_provider: str | None = None
    embedded_at: datetime | None = None


class KnowledgeDocumentCreate(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    uploaded_by_user_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    acl_subjects: list[str] = Field(default_factory=list)
    sensitivity_level: int = Field(default=0, ge=0)
    document_version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    storage_object_id: str | None = None
    chunks: list[DocumentChunkCreate] = Field(default_factory=list)


class KnowledgeDocumentApiCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str | None = None
    content_type: str = Field(default="text/plain", min_length=1)
    acl_subjects: list[str] = Field(default_factory=list)
    sensitivity_level: int = Field(default=0, ge=0)
    document_version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    chunks: list[DocumentChunkCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_content_or_chunks(self) -> "KnowledgeDocumentApiCreate":
        if (self.content is None or not self.content.strip()) and not self.chunks:
            raise ValueError("knowledge document requires content or chunks")
        return self


class KnowledgeDocument(BaseModel):
    id: str = Field(default_factory=lambda: new_id("knowledge_document"))
    tenant_id: str
    workspace_id: str
    knowledge_base_id: str
    source_uri: str
    source_document_id: str
    uploaded_by_user_id: str
    title: str
    acl_subjects: list[str] = Field(default_factory=list)
    sensitivity_level: int = 0
    document_version: str
    content_hash: str
    storage_object_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class DocumentChunk(BaseModel):
    id: str = Field(default_factory=lambda: new_id("knowledge_chunk"))
    tenant_id: str
    workspace_id: str
    knowledge_base_id: str
    document_id: str
    source_document_id: str
    source_uri: str
    content: str
    citation: dict[str, Any] = Field(default_factory=dict)
    acl_subjects: list[str] = Field(default_factory=list)
    sensitivity_level: int = 0
    embedding: list[float] = Field(default_factory=list)
    embedding_model: str | None = None
    embedding_provider: str | None = None
    embedded_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class RetrievalRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    allowed_workspace_ids: list[str] = Field(default_factory=list)
    acl_subjects: list[str] = Field(default_factory=list)
    clearance_level: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=50)
    query_embedding: list[float] = Field(default_factory=list)
    embedding_model: str | None = None


class KnowledgeQueryRequest(BaseModel):
    query: str = Field(min_length=1)
    allowed_workspace_ids: list[str] = Field(default_factory=list)
    acl_subjects: list[str] = Field(default_factory=list)
    clearance_level: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=50)


class RetrievalResult(BaseModel):
    document_id: str
    chunk_id: str
    source_document_id: str
    source_uri: str
    excerpt: str
    score: float
    citation: dict[str, Any] = Field(default_factory=dict)
    sensitivity_level: int = Field(default=0, ge=0)


class KnowledgeTenantDeletionResult(BaseModel):
    deleted_base_ids: list[str] = Field(default_factory=list)
    deleted_document_ids: list[str] = Field(default_factory=list)
    deleted_chunk_count: int = 0
