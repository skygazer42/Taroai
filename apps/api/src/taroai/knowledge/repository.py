import json
from datetime import datetime

from pydantic import BaseModel

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
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


class SqlKnowledgeService(BaseModel):
    config: DatabaseConfig

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
        with self._connect() as connection:
            self._ensure_tenant(connection, tenant_id)
            self._ensure_workspace(connection, tenant_id, request.workspace_id)
            connection.execute(
                """
                INSERT INTO knowledge_bases (
                    id, tenant_id, workspace_id, name, description,
                    created_by_user_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    knowledge_base.id,
                    knowledge_base.tenant_id,
                    knowledge_base.workspace_id,
                    knowledge_base.name,
                    knowledge_base.description,
                    knowledge_base.created_by_user_id,
                    self._dt(knowledge_base.created_at),
                ),
            )
        return knowledge_base

    def list_bases_for_tenant(self, tenant_id: str) -> list[KnowledgeBase]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_bases
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._base_from_row(row) for row in rows]

    def list_bases_for_workspace(self, tenant_id: str, workspace_id: str) -> list[KnowledgeBase]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_bases
                WHERE tenant_id = ? AND workspace_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id, workspace_id),
            ).fetchall()
        return [self._base_from_row(row) for row in rows]

    def list_documents(
        self,
        tenant_id: str,
        knowledge_base_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[KnowledgeDocument]:
        clauses = ["tenant_id = ?"]
        params = [tenant_id]
        if knowledge_base_id is not None:
            clauses.append("knowledge_base_id = ?")
            params.append(knowledge_base_id)
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM knowledge_documents
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at, id
                """,
                params,
            ).fetchall()
        return [self._document_from_row(row) for row in rows]

    def register_document(self, request: KnowledgeDocumentCreate) -> KnowledgeDocument:
        knowledge_base = self._get_base(request.tenant_id, request.knowledge_base_id)
        if knowledge_base.workspace_id != request.workspace_id:
            raise ValueError("Knowledge document workspace does not match knowledge base workspace")
        document = KnowledgeDocument(**request.model_dump(exclude={"chunks"}))
        chunks = [
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
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id FROM knowledge_documents
                WHERE tenant_id = ? AND content_hash = ?
                """,
                (request.tenant_id, request.content_hash),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Knowledge document content hash already exists: {request.content_hash}")
            connection.execute(
                """
                INSERT INTO knowledge_documents (
                    id, tenant_id, workspace_id, knowledge_base_id, source_uri,
                    source_document_id, uploaded_by_user_id, title, acl_subjects,
                    sensitivity_level, document_version, content_hash, storage_object_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.id,
                    document.tenant_id,
                    document.workspace_id,
                    document.knowledge_base_id,
                    document.source_uri,
                    document.source_document_id,
                    document.uploaded_by_user_id,
                    document.title,
                    self._json(document.acl_subjects),
                    document.sensitivity_level,
                    document.document_version,
                    document.content_hash,
                    document.storage_object_id,
                    self._dt(document.created_at),
                ),
            )
            for chunk in chunks:
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks (
                        id, tenant_id, workspace_id, knowledge_base_id, document_id,
                        source_document_id, source_uri, content, citation,
                        acl_subjects, sensitivity_level, embedding_vector,
                        embedding_model, embedding_provider, embedded_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.id,
                        chunk.tenant_id,
                        chunk.workspace_id,
                        chunk.knowledge_base_id,
                        chunk.document_id,
                        chunk.source_document_id,
                        chunk.source_uri,
                        chunk.content,
                        self._json(chunk.citation),
                        self._json(chunk.acl_subjects),
                        chunk.sensitivity_level,
                        self._json(chunk.embedding),
                        chunk.embedding_model,
                        chunk.embedding_provider,
                        self._dt(chunk.embedded_at) if chunk.embedded_at is not None else None,
                        self._dt(chunk.created_at),
                    ),
                )
        return document

    def list_chunks(self, tenant_id: str, document_id: str) -> list[DocumentChunk]:
        document = self._get_document_optional(document_id)
        if document is None:
            raise NotFoundError(f"Knowledge document not found: {document_id}")
        if document.tenant_id != tenant_id:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_chunks
                WHERE tenant_id = ? AND document_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id, document_id),
            ).fetchall()
        return [self._chunk_from_row(row) for row in rows]

    def retrieve(self, request: RetrievalRequest) -> list[RetrievalResult]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_chunks
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (request.tenant_id,),
            ).fetchall()
        return retrieve_chunks([self._chunk_from_row(row) for row in rows], request)

    def delete_for_tenant(self, tenant_id: str) -> KnowledgeTenantDeletionResult:
        with self._connect() as connection:
            base_rows = connection.execute(
                """
                SELECT id FROM knowledge_bases
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
            document_rows = connection.execute(
                """
                SELECT id FROM knowledge_documents
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
            chunk_count_row = connection.execute(
                "SELECT COUNT(*) AS count FROM knowledge_chunks WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            deleted_base_ids = [row["id"] for row in base_rows]
            deleted_document_ids = [row["id"] for row in document_rows]
            deleted_chunk_count = int(chunk_count_row["count"]) if chunk_count_row is not None else 0
            connection.execute("DELETE FROM knowledge_chunks WHERE tenant_id = ?", (tenant_id,))
            connection.execute("DELETE FROM knowledge_documents WHERE tenant_id = ?", (tenant_id,))
            connection.execute("DELETE FROM knowledge_bases WHERE tenant_id = ?", (tenant_id,))
        return KnowledgeTenantDeletionResult(
            deleted_base_ids=deleted_base_ids,
            deleted_document_ids=deleted_document_ids,
            deleted_chunk_count=deleted_chunk_count,
        )

    def _get_base(self, tenant_id: str, knowledge_base_id: str) -> KnowledgeBase:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM knowledge_bases
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, knowledge_base_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Knowledge base not found: {knowledge_base_id}")
        return self._base_from_row(row)

    def _get_document_optional(self, document_id: str) -> KnowledgeDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            return None
        return self._document_from_row(row)

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _ensure_workspace(self, connection, tenant_id: str, workspace_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at) VALUES (?, ?, ?, ?)",
            (workspace_id, tenant_id, workspace_id, self._dt(utc_now())),
        )

    def _base_from_row(self, row) -> KnowledgeBase:
        return KnowledgeBase(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            description=row["description"],
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _document_from_row(self, row) -> KnowledgeDocument:
        return KnowledgeDocument(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            knowledge_base_id=row["knowledge_base_id"],
            source_uri=row["source_uri"],
            source_document_id=row["source_document_id"],
            uploaded_by_user_id=row["uploaded_by_user_id"],
            title=row["title"],
            acl_subjects=self._loads(row["acl_subjects"]),
            sensitivity_level=row["sensitivity_level"],
            document_version=row["document_version"],
            content_hash=row["content_hash"],
            storage_object_id=row["storage_object_id"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _chunk_from_row(self, row) -> DocumentChunk:
        return DocumentChunk(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            knowledge_base_id=row["knowledge_base_id"],
            document_id=row["document_id"],
            source_document_id=row["source_document_id"],
            source_uri=row["source_uri"],
            content=row["content"],
            citation=self._loads(row["citation"]),
            acl_subjects=self._loads(row["acl_subjects"]),
            sensitivity_level=row["sensitivity_level"],
            embedding=self._loads(row["embedding_vector"]),
            embedding_model=row["embedding_model"],
            embedding_provider=row["embedding_provider"],
            embedded_at=(
                self._parse_dt(row["embedded_at"]) if row["embedded_at"] is not None else None
            ),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _json(self, value) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value):
        if value is None:
            return []
        if not isinstance(value, str):
            return value
        return json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)
