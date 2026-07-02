from datetime import datetime, timedelta, timezone

from taroai.config import Settings
from taroai.workers import (
    BillingAggregationJob,
    CleanupJob,
    InMemoryJobQueue,
    JobStatus,
    JobType,
    RedisJobQueue,
    RunExecutionJob,
)


class RecordingRedisClient:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

    def hset(self, name: str, key: str, value: str) -> None:
        self.hashes.setdefault(name, {})[key] = value

    def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(key)

    def rpush(self, name: str, value: str) -> None:
        self.lists.setdefault(name, []).append(value)

    def lpop(self, name: str) -> str | None:
        values = self.lists.setdefault(name, [])
        if not values:
            return None
        return values.pop(0)

    def lrange(self, name: str, start: int, end: int) -> list[str]:
        values = self.lists.setdefault(name, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]


def test_worker_jobs_are_pydantic_and_tenant_scoped():
    run_job = RunExecutionJob(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        run_id="run_123",
        requested_by_user_id="user_1",
    )
    billing_job = BillingAggregationJob(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        billing_period="2026-07",
    )
    cleanup_job = CleanupJob(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        older_than_days=30,
        resource_types=["runtime_states", "short_term_memory"],
    )

    assert run_job.model_dump()["run_id"] == "run_123"
    assert billing_job.billing_period == "2026-07"
    assert cleanup_job.resource_types == ["runtime_states", "short_term_memory"]


def test_job_queue_claim_ack_and_fail_lifecycle():
    queue = InMemoryJobQueue()

    first = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            requested_by_user_id="user_1",
        ),
    )
    second = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_2",
            requested_by_user_id="user_1",
        ),
    )

    claimed_first = queue.claim(JobType.RUN_EXECUTION, worker_id="agent_worker_1")
    claimed_second = queue.claim(JobType.RUN_EXECUTION, worker_id="agent_worker_1")

    assert claimed_first is not None
    assert claimed_second is not None
    assert [claimed_first.id, claimed_second.id] == [first.id, second.id]
    assert claimed_first.status == JobStatus.RUNNING
    assert claimed_first.worker_id == "agent_worker_1"

    queue.ack(claimed_first.id)
    failed = queue.fail(claimed_second.id, "model gateway unavailable")

    assert queue.get(claimed_first.id).status == JobStatus.SUCCEEDED
    assert failed.status == JobStatus.FAILED
    assert failed.error == "model gateway unavailable"
    assert queue.claim(JobType.RUN_EXECUTION, worker_id="agent_worker_1") is None


def test_queue_settings_define_redis_worker_contract():
    settings = Settings(_env_file=None)

    assert settings.job_queue_backend == "disabled"
    assert settings.run_execution_queue_name == "runs.execute"
    assert settings.billing_queue_name == "billing.aggregate"
    assert settings.cleanup_queue_name == "system.cleanup"
    assert settings.worker_job_lease_seconds == 300
    assert settings.worker_job_retry_delay_seconds == 30
    assert settings.worker_job_max_attempts == 3


def test_job_queue_reject_retries_until_dead_letter():
    queue = InMemoryJobQueue()
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_retry",
            requested_by_user_id="user_1",
        ),
        now=now,
        max_attempts=2,
    )

    first_attempt = queue.claim(JobType.RUN_EXECUTION, worker_id="agent_worker_1", now=now)
    retried = queue.reject(
        first_attempt.id,
        "transient model gateway outage",
        now=now,
        retry_delay_seconds=30,
    )
    early_claim = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_1",
        now=now + timedelta(seconds=29),
    )
    second_attempt = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_1",
        now=now + timedelta(seconds=30),
    )
    dead_letter = queue.reject(
        second_attempt.id,
        "model gateway still unavailable",
        now=now + timedelta(seconds=30),
        retry_delay_seconds=30,
    )

    assert retried.status == JobStatus.PENDING
    assert retried.attempts == 1
    assert retried.available_at == now + timedelta(seconds=30)
    assert early_claim is None
    assert second_attempt.attempts == 2
    assert dead_letter.status == JobStatus.DEAD_LETTER
    assert dead_letter.completed_at == now + timedelta(seconds=30)
    assert queue.list_dead_letters(JobType.RUN_EXECUTION) == [dead_letter]


def test_redis_job_queue_persists_jobs_in_hash_and_pending_list():
    client = RecordingRedisClient()
    queue = RedisJobQueue(url="redis://localhost:6379/0", key_prefix="taroai:test", client=client)

    job = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_redis",
            requested_by_user_id="user_1",
        ),
    )
    claimed = queue.claim(JobType.RUN_EXECUTION, worker_id="agent_worker_redis", lease_seconds=45)

    assert client.lists["taroai:test:runs.execute:pending"] == []
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.worker_id == "agent_worker_redis"
    assert claimed.lease_expires_at is not None
    assert queue.get(job.id).status == JobStatus.RUNNING

    completed = queue.ack(job.id)

    assert completed.status == JobStatus.SUCCEEDED
    assert queue.get(job.id).completed_at is not None


def test_redis_job_queue_dead_letters_after_retry_budget_is_exhausted():
    client = RecordingRedisClient()
    queue = RedisJobQueue(url="redis://localhost:6379/0", key_prefix="taroai:test", client=client)
    job = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_dead_letter",
            requested_by_user_id="user_1",
        ),
        max_attempts=1,
    )
    claimed = queue.claim(JobType.RUN_EXECUTION, worker_id="agent_worker_redis")

    rejected = queue.reject(claimed.id, "permanent failure")

    assert rejected.status == JobStatus.DEAD_LETTER
    assert client.lists["taroai:test:runs.execute:dead"] == [job.id]
    assert queue.list_dead_letters(JobType.RUN_EXECUTION) == [rejected]
