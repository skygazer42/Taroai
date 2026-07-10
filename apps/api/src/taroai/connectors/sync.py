from typing import Literal

from pydantic import BaseModel, Field

from taroai.connectors.models import ConnectorSyncStatus
from taroai.knowledge import DocumentChunkCreate, KnowledgeDocumentCreate


SourceAclPrincipalType = Literal["user", "group", "domain", "role", "service_account"]


class SourceAclPrincipal(BaseModel):
    source_principal_id: str = Field(min_length=1)
    principal_type: SourceAclPrincipalType


class ConnectorAclMappingRule(BaseModel):
    source_principal_id: str = Field(min_length=1)
    acl_subject: str = Field(min_length=1)


class ConnectorAclMapping(BaseModel):
    rules: list[ConnectorAclMappingRule] = Field(default_factory=list)

    def map_principals(
        self,
        principals: list[SourceAclPrincipal],
    ) -> list[str]:
        by_source_id = {
            rule.source_principal_id: rule.acl_subject
            for rule in self.rules
        }
        subjects: list[str] = []
        for principal in principals:
            subject = by_source_id.get(principal.source_principal_id)
            if subject is not None and subject not in subjects:
                subjects.append(subject)
        return subjects


class ConnectorSyncDocument(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    connector_id: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    document_version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    sensitivity_level: int = Field(default=0, ge=0)
    source_acl: list[SourceAclPrincipal] = Field(default_factory=list)
    chunks: list[DocumentChunkCreate] = Field(default_factory=list)


class ConnectorSyncJobCreate(BaseModel):
    knowledge_base_id: str = Field(min_length=1)
    documents: list[ConnectorSyncDocument] = Field(default_factory=list)
    acl_mapping: ConnectorAclMapping = Field(default_factory=ConnectorAclMapping)
    cursor: str | None = None


class ConnectorSyncJob(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    connector_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)
    documents: list[ConnectorSyncDocument] = Field(default_factory=list)
    acl_mapping: ConnectorAclMapping = Field(default_factory=ConnectorAclMapping)
    cursor: str | None = None
    status: ConnectorSyncStatus = ConnectorSyncStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    error_code: str | None = None


class ConnectorKnowledgeIngestionPlan(BaseModel):
    connector_id: str = Field(min_length=1)
    knowledge_document: KnowledgeDocumentCreate
    memory_write_count: int = 0


class ConnectorSyncPlanner(BaseModel):
    acl_mapping: ConnectorAclMapping = Field(default_factory=ConnectorAclMapping)

    def plan_knowledge_ingestion(
        self,
        document: ConnectorSyncDocument,
        uploaded_by_user_id: str,
        knowledge_base_id: str,
    ) -> ConnectorKnowledgeIngestionPlan:
        return ConnectorKnowledgeIngestionPlan(
            connector_id=document.connector_id,
            knowledge_document=KnowledgeDocumentCreate(
                tenant_id=document.tenant_id,
                workspace_id=document.workspace_id,
                knowledge_base_id=knowledge_base_id,
                source_uri=document.source_uri,
                source_document_id=document.source_document_id,
                uploaded_by_user_id=uploaded_by_user_id,
                title=document.title,
                acl_subjects=self.acl_mapping.map_principals(document.source_acl),
                sensitivity_level=document.sensitivity_level,
                document_version=document.document_version,
                content_hash=document.content_hash,
                chunks=document.chunks,
            ),
            memory_write_count=0,
        )
