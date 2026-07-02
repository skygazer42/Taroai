from datetime import datetime
from typing import Any

from pydantic import BaseModel

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.domain import utc_now
from taroai.storage import StorageLifecycleCleanupRequest, StorageLifecycleService
from taroai.workers.models import CleanupJob, JobEnvelope, JobType
from taroai.workers.queue import JobQueue


STORAGE_RESOURCE_TYPE = "storage_objects"


class CleanupWorker(BaseModel):
    queue: JobQueue
    storage_lifecycle_service: StorageLifecycleService
    audit_service: AuditService | None = None
    worker_id: str = "cleanup_worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30

    def process_next(self, now: datetime | None = None) -> JobEnvelope | None:
        resolved_now = now or utc_now()
        job = self.queue.claim(
            JobType.CLEANUP,
            worker_id=self.worker_id,
            now=resolved_now,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        payload = CleanupJob.model_validate(job.payload)
        self._record_job_audit("worker.job.started", job, payload)
        try:
            cleanup_result = self._cleanup(payload, resolved_now)
        except Exception as error:
            rejected = self.queue.reject(
                job.id,
                str(error),
                now=resolved_now,
                retry_delay_seconds=self.retry_delay_seconds,
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
        self._record_job_audit(
            "worker.job.succeeded",
            completed,
            payload,
            cleanup_result,
        )
        return completed

    def _cleanup(self, payload: CleanupJob, now: datetime) -> dict[str, Any]:
        if payload.resource_types and STORAGE_RESOURCE_TYPE not in payload.resource_types:
            return {
                "deleted_count": 0,
                "storage_object_ids": [],
            }
        result = self.storage_lifecycle_service.cleanup_expired_objects(
            StorageLifecycleCleanupRequest(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                now=now,
            )
        )
        return result.model_dump(mode="json")

    def _record_job_audit(
        self,
        event_type: str,
        job: JobEnvelope,
        payload: CleanupJob,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                user_id=None,
                run_id=None,
                event_type=event_type,
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    "older_than_days": payload.older_than_days,
                    "resource_types": payload.resource_types,
                    "attempts": job.attempts,
                    **(metadata or {}),
                },
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=None,
                    actor_type="worker",
                ),
            )
        )
