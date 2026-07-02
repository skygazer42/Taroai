from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import utc_now
from taroai.store import NotFoundError
from taroai.workers.models import JobEnvelope, JobStatus, JobType


class RedisQueueConfigurationError(RuntimeError):
    pass


class JobQueue(BaseModel):
    def enqueue(
        self,
        job_type: JobType,
        payload: BaseModel,
        now: datetime | None = None,
        max_attempts: int = 3,
    ) -> JobEnvelope:
        raise NotImplementedError

    def claim(
        self,
        job_type: JobType,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: int = 300,
    ) -> JobEnvelope | None:
        raise NotImplementedError

    def ack(self, job_id: str, now: datetime | None = None) -> JobEnvelope:
        raise NotImplementedError

    def fail(self, job_id: str, error: str, now: datetime | None = None) -> JobEnvelope:
        raise NotImplementedError

    def reject(
        self,
        job_id: str,
        error: str,
        now: datetime | None = None,
        retry_delay_seconds: int = 30,
    ) -> JobEnvelope:
        raise NotImplementedError

    def get(self, job_id: str) -> JobEnvelope:
        raise NotImplementedError

    def list_dead_letters(self, job_type: JobType | None = None) -> list[JobEnvelope]:
        raise NotImplementedError


class InMemoryJobQueue(JobQueue):
    jobs: list[JobEnvelope] = Field(default_factory=list)

    def enqueue(
        self,
        job_type: JobType,
        payload: BaseModel,
        now: datetime | None = None,
        max_attempts: int = 3,
    ) -> JobEnvelope:
        resolved_now = now or utc_now()
        job = JobEnvelope(
            type=job_type,
            payload=payload.model_dump(mode="json"),
            created_at=resolved_now,
            available_at=resolved_now,
            max_attempts=max_attempts,
        )
        self.jobs.append(job)
        return job

    def claim(
        self,
        job_type: JobType,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: int = 300,
    ) -> JobEnvelope | None:
        resolved_now = now or utc_now()
        for index, job in enumerate(self.jobs):
            if job.type != job_type:
                continue
            if job.status != JobStatus.PENDING:
                continue
            if job.available_at > resolved_now:
                continue
            claimed = job.model_copy(
                update={
                    "status": JobStatus.RUNNING,
                    "worker_id": worker_id,
                    "started_at": resolved_now,
                    "lease_expires_at": resolved_now + timedelta(seconds=lease_seconds),
                    "attempts": job.attempts + 1,
                    "error": None,
                }
            )
            self.jobs[index] = claimed
            return claimed
        return None

    def ack(self, job_id: str, now: datetime | None = None) -> JobEnvelope:
        job = self.get(job_id)
        updated = job.model_copy(
            update={
                "status": JobStatus.SUCCEEDED,
                "completed_at": now or utc_now(),
                "lease_expires_at": None,
                "error": None,
            }
        )
        self._replace(updated)
        return updated

    def fail(self, job_id: str, error: str, now: datetime | None = None) -> JobEnvelope:
        job = self.get(job_id)
        updated = job.model_copy(
            update={
                "status": JobStatus.FAILED,
                "completed_at": now or utc_now(),
                "lease_expires_at": None,
                "error": error,
            }
        )
        self._replace(updated)
        return updated

    def reject(
        self,
        job_id: str,
        error: str,
        now: datetime | None = None,
        retry_delay_seconds: int = 30,
    ) -> JobEnvelope:
        resolved_now = now or utc_now()
        job = self.get(job_id)
        if job.attempts < job.max_attempts:
            updated = job.model_copy(
                update={
                    "status": JobStatus.PENDING,
                    "available_at": resolved_now + timedelta(seconds=retry_delay_seconds),
                    "lease_expires_at": None,
                    "worker_id": None,
                    "completed_at": None,
                    "error": error,
                }
            )
        else:
            updated = job.model_copy(
                update={
                    "status": JobStatus.DEAD_LETTER,
                    "completed_at": resolved_now,
                    "lease_expires_at": None,
                    "error": error,
                }
            )
        self._replace(updated)
        return updated

    def get(self, job_id: str) -> JobEnvelope:
        for job in self.jobs:
            if job.id == job_id:
                return job
        raise NotFoundError(f"Job not found: {job_id}")

    def list_dead_letters(self, job_type: JobType | None = None) -> list[JobEnvelope]:
        return [
            job
            for job in self.jobs
            if job.status == JobStatus.DEAD_LETTER and (job_type is None or job.type == job_type)
        ]

    def _replace(self, updated: JobEnvelope) -> None:
        for index, job in enumerate(self.jobs):
            if job.id == updated.id:
                self.jobs[index] = updated
                return
        raise NotFoundError(f"Job not found: {updated.id}")


class RedisJobQueue(JobQueue):
    url: str = Field(min_length=1)
    key_prefix: str = Field(default="taroai:jobs", min_length=1)
    client: Any | None = None

    def enqueue(
        self,
        job_type: JobType,
        payload: BaseModel,
        now: datetime | None = None,
        max_attempts: int = 3,
    ) -> JobEnvelope:
        resolved_now = now or utc_now()
        job = JobEnvelope(
            type=job_type,
            payload=payload.model_dump(mode="json"),
            created_at=resolved_now,
            available_at=resolved_now,
            max_attempts=max_attempts,
        )
        client = self._client()
        self._write(client, job)
        client.rpush(self._pending_key(job_type), job.id)
        return job

    def claim(
        self,
        job_type: JobType,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: int = 300,
    ) -> JobEnvelope | None:
        resolved_now = now or utc_now()
        client = self._client()
        while True:
            job_id = client.lpop(self._pending_key(job_type))
            if job_id is None:
                return None
            job = self._read_or_none(client, self._decode(job_id))
            if job is None or job.status != JobStatus.PENDING:
                continue
            if job.available_at > resolved_now:
                client.rpush(self._pending_key(job_type), job.id)
                return None
            claimed = job.model_copy(
                update={
                    "status": JobStatus.RUNNING,
                    "worker_id": worker_id,
                    "started_at": resolved_now,
                    "lease_expires_at": resolved_now + timedelta(seconds=lease_seconds),
                    "attempts": job.attempts + 1,
                    "error": None,
                }
            )
            self._write(client, claimed)
            return claimed

    def ack(self, job_id: str, now: datetime | None = None) -> JobEnvelope:
        client = self._client()
        job = self._read(client, job_id)
        updated = job.model_copy(
            update={
                "status": JobStatus.SUCCEEDED,
                "completed_at": now or utc_now(),
                "lease_expires_at": None,
                "error": None,
            }
        )
        self._write(client, updated)
        return updated

    def fail(self, job_id: str, error: str, now: datetime | None = None) -> JobEnvelope:
        client = self._client()
        job = self._read(client, job_id)
        updated = job.model_copy(
            update={
                "status": JobStatus.FAILED,
                "completed_at": now or utc_now(),
                "lease_expires_at": None,
                "error": error,
            }
        )
        self._write(client, updated)
        return updated

    def reject(
        self,
        job_id: str,
        error: str,
        now: datetime | None = None,
        retry_delay_seconds: int = 30,
    ) -> JobEnvelope:
        resolved_now = now or utc_now()
        client = self._client()
        job = self._read(client, job_id)
        if job.attempts < job.max_attempts:
            updated = job.model_copy(
                update={
                    "status": JobStatus.PENDING,
                    "available_at": resolved_now + timedelta(seconds=retry_delay_seconds),
                    "lease_expires_at": None,
                    "worker_id": None,
                    "completed_at": None,
                    "error": error,
                }
            )
            self._write(client, updated)
            client.rpush(self._pending_key(updated.type), updated.id)
            return updated
        updated = job.model_copy(
            update={
                "status": JobStatus.DEAD_LETTER,
                "completed_at": resolved_now,
                "lease_expires_at": None,
                "error": error,
            }
        )
        self._write(client, updated)
        client.rpush(self._dead_key(updated.type), updated.id)
        return updated

    def get(self, job_id: str) -> JobEnvelope:
        return self._read(self._client(), job_id)

    def list_dead_letters(self, job_type: JobType | None = None) -> list[JobEnvelope]:
        client = self._client()
        if job_type is not None:
            return [
                self._read(client, self._decode(job_id))
                for job_id in client.lrange(self._dead_key(job_type), 0, -1)
            ]
        dead_letters: list[JobEnvelope] = []
        for current_type in JobType:
            dead_letters.extend(self.list_dead_letters(current_type))
        return dead_letters

    def _client(self):
        if self.client is not None:
            return self.client
        try:
            import redis
        except ImportError as error:
            raise RedisQueueConfigurationError("redis package is required for RedisJobQueue") from error
        client = redis.Redis.from_url(self.url, decode_responses=True)
        object.__setattr__(self, "client", client)
        return client

    def _pending_key(self, job_type: JobType) -> str:
        return f"{self.key_prefix}:{job_type.value}:pending"

    def _dead_key(self, job_type: JobType) -> str:
        return f"{self.key_prefix}:{job_type.value}:dead"

    def _jobs_key(self) -> str:
        return f"{self.key_prefix}:jobs"

    def _write(self, client, job: JobEnvelope) -> None:
        client.hset(self._jobs_key(), job.id, job.model_dump_json())

    def _read(self, client, job_id: str) -> JobEnvelope:
        job = self._read_or_none(client, job_id)
        if job is None:
            raise NotFoundError(f"Job not found: {job_id}")
        return job

    def _read_or_none(self, client, job_id: str) -> JobEnvelope | None:
        raw = client.hget(self._jobs_key(), job_id)
        if raw is None:
            return None
        return JobEnvelope.model_validate_json(self._decode(raw))

    def _decode(self, value: str | bytes) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value
