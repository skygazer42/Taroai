from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.connectors import (
    ConnectorSyncJob,
    ConnectorSyncPlanner,
    ConnectorSyncStateUpdate,
    ConnectorSyncStatus,
)
from taroai.domain import RunStatus, utc_now
from taroai.workers.models import JobEnvelope, JobType
from taroai.workers.queue import JobQueue


CONNECTOR_SYNC_DOCUMENT_METER = "connector_sync_document_count"


class ConnectorSyncWorker(BaseModel):
    queue: JobQueue
    knowledge_service: Any
    store: Any
    connector_registry: Any | None = None
    audit_service: AuditService | None = None
    worker_id: str = "connector_sync_worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30
    max_attempts: int = Field(default=3, ge=1)

    def process_next(self, now: datetime | None = None) -> JobEnvelope | None:
        resolved_now = now or utc_now()
        job = self.queue.claim(
            JobType.CONNECTOR_SYNC,
            worker_id=self.worker_id,
            now=resolved_now,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None

        payload = ConnectorSyncJob.model_validate(job.payload)
        self._update_sync_state(
            payload,
            job,
            status=ConnectorSyncStatus.RUNNING,
            started_at=resolved_now,
        )
        self._record_job_audit("worker.job.started", job, payload)
        try:
            result = self._sync_documents(payload)
        except Exception as error:
            rejected = self.queue.reject(
                job.id,
                str(error),
                now=resolved_now,
                retry_delay_seconds=self.retry_delay_seconds,
            )
            self._mark_run_failed(payload, str(error))
            self._update_sync_state(
                payload,
                rejected,
                status=ConnectorSyncStatus.FAILED,
                completed_at=resolved_now,
                error_code=error.__class__.__name__,
            )
            self._record_job_audit(
                "worker.job.failed",
                rejected,
                payload,
                {
                    "error_type": error.__class__.__name__,
                    "error": str(error),
                    "final_job_status": rejected.status.value,
                },
            )
            return rejected

        completed = self.queue.ack(job.id, now=resolved_now)
        self._mark_run_succeeded(payload, result)
        self._update_sync_state(
            payload,
            completed,
            status=ConnectorSyncStatus.SUCCEEDED,
            completed_at=resolved_now,
        )
        self._record_job_audit(
            "worker.job.succeeded",
            completed,
            payload,
            result,
        )
        return completed

    def _sync_documents(self, payload: ConnectorSyncJob) -> dict[str, Any]:
        planner = ConnectorSyncPlanner(acl_mapping=payload.acl_mapping)
        document_ids: list[str] = []
        chunk_count = 0
        for document in payload.documents:
            plan = planner.plan_knowledge_ingestion(
                document=document,
                uploaded_by_user_id=payload.requested_by_user_id,
                knowledge_base_id=payload.knowledge_base_id,
            )
            registered = self.knowledge_service.register_document(plan.knowledge_document)
            document_ids.append(registered.id)
            chunk_count += len(plan.knowledge_document.chunks)

        metadata = {
            "connector_id": payload.connector_id,
            "knowledge_base_id": payload.knowledge_base_id,
            "document_count": len(document_ids),
            "chunk_count": chunk_count,
            "cursor": payload.cursor,
        }
        self.store.record_billing_meter(
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            meter_type=CONNECTOR_SYNC_DOCUMENT_METER,
            quantity=len(document_ids),
            unit="document",
            metadata=metadata,
        )
        self._record_connector_audit("connector.sync_completed", payload, metadata)
        return metadata | {"document_ids": document_ids}

    def _mark_run_succeeded(
        self,
        payload: ConnectorSyncJob,
        result: dict[str, Any],
    ) -> None:
        run = self.store.update_run_status(
            payload.tenant_id,
            payload.run_id,
            RunStatus.SUCCEEDED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "connector.sync_completed",
            {
                "connector_id": payload.connector_id,
                "knowledge_base_id": payload.knowledge_base_id,
                "document_count": result["document_count"],
                "chunk_count": result["chunk_count"],
                "cursor": payload.cursor,
            },
        )

    def _mark_run_failed(self, payload: ConnectorSyncJob, error: str) -> None:
        run = self.store.update_run_status(
            payload.tenant_id,
            payload.run_id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "connector.sync_failed",
            {
                "connector_id": payload.connector_id,
                "knowledge_base_id": payload.knowledge_base_id,
                "error": error,
            },
        )

    def _update_sync_state(
        self,
        payload: ConnectorSyncJob,
        job: JobEnvelope,
        status: ConnectorSyncStatus,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error_code: str | None = None,
    ) -> None:
        if self.connector_registry is None:
            return
        self.connector_registry.update_connector_sync_state(
            payload.tenant_id,
            payload.connector_id,
            ConnectorSyncStateUpdate(
                status=status,
                run_id=payload.run_id,
                job_id=job.id,
                knowledge_base_id=payload.knowledge_base_id,
                cursor=payload.cursor,
                started_at=started_at,
                completed_at=completed_at,
                error_code=error_code,
            ),
        )

    def _record_connector_audit(
        self,
        event_type: str,
        payload: ConnectorSyncJob,
        metadata: dict[str, Any],
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                user_id=payload.requested_by_user_id,
                run_id=payload.run_id,
                event_type=event_type,
                metadata=metadata,
            )
        )

    def _record_job_audit(
        self,
        event_type: str,
        job: JobEnvelope,
        payload: ConnectorSyncJob,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                user_id=payload.requested_by_user_id,
                run_id=payload.run_id,
                event_type=event_type,
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    "connector_id": payload.connector_id,
                    "knowledge_base_id": payload.knowledge_base_id,
                    "document_count": len(payload.documents),
                    "attempts": job.attempts,
                    **(metadata or {}),
                },
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=payload.requested_by_user_id,
                    actor_type="worker",
                ),
            )
        )
