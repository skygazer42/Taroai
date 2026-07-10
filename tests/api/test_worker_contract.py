from datetime import datetime, timedelta, timezone

from pathlib import Path

from taroai.config import Settings
from taroai.deployment import RestoreDrillVerificationConfig
from taroai.deployment_evidence import RestoreDrillVerificationResult
from taroai.workers import (
    BillingAggregationJob,
    CleanupJob,
    InMemoryJobQueue,
    JobStatus,
    JobType,
    RedisJobQueue,
    RestoreDrillDueJob,
    RestoreDrillEvidenceCollectionJob,
    RestoreDrillExecutionJob,
    RunExecutionJob,
    TriggerDueJob,
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

    def hvals(self, name: str) -> list[str]:
        return list(self.hashes.get(name, {}).values())


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
    trigger_job = TriggerDueJob(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        trigger_id="trigger_123",
        trigger_type="schedule",
        scheduled_for=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        requested_by_user_id="svc_scheduler",
    )
    restore_drill_job = RestoreDrillDueJob(
        tenant_id="tenant_acme",
        workspace_id="workspace_ops",
        schedule_id="restore_drill_schedule_123",
        scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
        requested_by_user_id="svc_restore_drill",
        runbook_ref="docs/operations/disaster-recovery.md",
    )
    restore_drill_evidence_job = RestoreDrillEvidenceCollectionJob(
        tenant_id="tenant_acme",
        workspace_id="workspace_ops",
        schedule_id="restore_drill_schedule_123",
        run_record_id="restore_drill_run_123",
        requested_by_user_id="svc_restore_drill",
        verification=RestoreDrillVerificationResult(
            drill_id="restore_drill_2026_07",
            backup_manifest_generated=True,
            restore_order_executed=True,
            database_restore_verified=True,
            object_storage_restore_verified=True,
            redis_restore_or_rebuild_verified=True,
            config_restore_verified=True,
            post_restore_validation_passed=True,
            rpo_minutes=5,
            rto_minutes=22,
        ),
    )
    restore_drill_execution_job = RestoreDrillExecutionJob(
        tenant_id="tenant_acme",
        workspace_id="workspace_ops",
        schedule_id="restore_drill_schedule_123",
        run_record_id="restore_drill_run_123",
        requested_by_user_id="svc_restore_drill",
        verification_config=RestoreDrillVerificationConfig(
            drill_id="restore_drill_2026_07",
            backup_manifest_path=Path("/restore/evidence/backup-manifest.json"),
            executed_restore_order=["postgres", "object_storage", "redis"],
            migration_plan_path=Path("/restore/evidence/migration-plan.json"),
            object_storage_verification_path=Path(
                "/restore/evidence/object-storage-verification.json"
            ),
            redis_queue_verification_path=Path(
                "/restore/evidence/redis-verification.json"
            ),
            config_restored=True,
            post_restore_checks_passed=True,
            rpo_minutes=5,
            rto_minutes=22,
        ),
    )

    assert run_job.model_dump()["run_id"] == "run_123"
    assert billing_job.billing_period == "2026-07"
    assert cleanup_job.resource_types == ["runtime_states", "short_term_memory"]
    assert trigger_job.trigger_id == "trigger_123"
    assert restore_drill_job.schedule_id == "restore_drill_schedule_123"
    assert restore_drill_evidence_job.run_record_id == "restore_drill_run_123"
    assert restore_drill_execution_job.verification_config.drill_id == (
        "restore_drill_2026_07"
    )
    assert JobType.RESTORE_DRILL_DUE.value == "restore_drill.due"
    assert JobType.RESTORE_DRILL_EXECUTION.value == "restore_drill.execute"
    assert JobType.RESTORE_DRILL_EVIDENCE.value == "restore_drill.evidence"


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
    assert settings.trigger_queue_name == "triggers.due"
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


def test_job_queue_reclaims_expired_lease_for_retry():
    queue = InMemoryJobQueue()
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    job = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_expired_lease",
            requested_by_user_id="user_1",
        ),
        now=now,
        max_attempts=2,
    )

    first_claim = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_1",
        now=now,
        lease_seconds=10,
    )
    early_claim = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_2",
        now=now + timedelta(seconds=9),
    )
    recovered_claim = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_2",
        now=now + timedelta(seconds=10),
        lease_seconds=10,
    )

    assert first_claim.id == job.id
    assert early_claim is None
    assert recovered_claim.id == job.id
    assert recovered_claim.status == JobStatus.RUNNING
    assert recovered_claim.worker_id == "agent_worker_2"
    assert recovered_claim.attempts == 2
    assert recovered_claim.started_at == now + timedelta(seconds=10)
    assert recovered_claim.error is None


def test_job_queue_dead_letters_expired_lease_after_retry_budget_is_exhausted():
    queue = InMemoryJobQueue()
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    job = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_expired_dead_letter",
            requested_by_user_id="user_1",
        ),
        now=now,
        max_attempts=1,
    )

    queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_1",
        now=now,
        lease_seconds=10,
    )

    reaped = queue.reap_expired_leases(
        JobType.RUN_EXECUTION,
        now=now + timedelta(seconds=10),
        error="worker lease expired",
    )

    assert [item.id for item in reaped] == [job.id]
    assert reaped[0].status == JobStatus.DEAD_LETTER
    assert reaped[0].completed_at == now + timedelta(seconds=10)
    assert queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_2",
        now=now + timedelta(seconds=10),
    ) is None
    assert queue.list_dead_letters(JobType.RUN_EXECUTION) == [reaped[0]]


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


def test_redis_job_queue_reclaims_expired_leases_before_claiming():
    client = RecordingRedisClient()
    queue = RedisJobQueue(url="redis://localhost:6379/0", key_prefix="taroai:test", client=client)
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    job = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_redis_expired_lease",
            requested_by_user_id="user_1",
        ),
        now=now,
        max_attempts=2,
    )

    first_claim = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_1",
        now=now,
        lease_seconds=10,
    )
    early_claim = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_2",
        now=now + timedelta(seconds=9),
    )
    recovered_claim = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id="agent_worker_2",
        now=now + timedelta(seconds=10),
        lease_seconds=10,
    )

    assert first_claim.id == job.id
    assert early_claim is None
    assert recovered_claim.id == job.id
    assert recovered_claim.worker_id == "agent_worker_2"
    assert recovered_claim.attempts == 2
    assert client.lists["taroai:test:runs.execute:pending"] == []
