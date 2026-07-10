import argparse
import json
from datetime import timedelta
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.domain import utc_now
from taroai.workers.models import JobStatus, JobType, RunExecutionJob
from taroai.workers.queue import RedisJobQueue, RedisQueueConfigurationError


class RedisQueueVerificationConfig(BaseModel):
    redis_url: str = Field(min_length=1)
    key_prefix: str = Field(default_factory=lambda: f"taroai:verify:{uuid4().hex[:12]}")
    tenant_id: str = Field(default="tenant_redis_verify", min_length=1)
    workspace_id: str = Field(default="workspace_redis_verify", min_length=1)
    user_id: str = Field(default="user_redis_verify", min_length=1)
    worker_id: str = Field(default="worker_redis_verify", min_length=1)
    lease_seconds: int = Field(default=45, ge=1)
    retry_delay_seconds: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def validate_redis_url(self) -> "RedisQueueVerificationConfig":
        scheme = urlparse(self.redis_url).scheme
        if scheme not in {"redis", "rediss", "unix"}:
            raise ValueError("Redis verification requires a Redis URL")
        return self


class RedisQueueVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_prefix: str
    ping_ok: bool
    acknowledged_job_id: str
    acknowledged_job_status: JobStatus
    recovered_job_id: str
    recovered_job_status: JobStatus
    recovered_job_attempts: int
    dead_letter_job_id: str
    dead_letter_job_status: JobStatus
    dead_letter_count: int


def parse_args(argv: list[str] | None = None) -> RedisQueueVerificationConfig:
    parser = argparse.ArgumentParser(description="Verify Redis-backed worker queue behavior.")
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--key-prefix", default=None)
    parsed = parser.parse_args(argv)
    config_data = {"redis_url": parsed.redis_url}
    if parsed.key_prefix is not None:
        config_data["key_prefix"] = parsed.key_prefix
    return RedisQueueVerificationConfig(**config_data)


def connect_redis(redis_url: str):
    try:
        import redis
    except ImportError as error:
        raise RedisQueueConfigurationError("redis package is required for RedisJobQueue") from error
    return redis.Redis.from_url(redis_url, decode_responses=True)


def verify_redis_queue(config: RedisQueueVerificationConfig) -> RedisQueueVerificationResult:
    client = connect_redis(config.redis_url)
    ping_ok = bool(client.ping())
    cleanup_verification_keys(config, client)
    queue = RedisJobQueue(url=config.redis_url, key_prefix=config.key_prefix, client=client)
    try:
        acknowledged = verify_ack_lifecycle(config, queue)
        recovered = verify_expired_lease_recovery(config, queue)
        dead_letter = verify_dead_letter_lifecycle(config, queue)
        dead_letters = queue.list_dead_letters(JobType.RUN_EXECUTION)
        if dead_letter.id not in {job.id for job in dead_letters}:
            raise RuntimeError("Redis queue dead-letter list did not include rejected job")
        return RedisQueueVerificationResult(
            key_prefix=config.key_prefix,
            ping_ok=ping_ok,
            acknowledged_job_id=acknowledged.id,
            acknowledged_job_status=acknowledged.status,
            recovered_job_id=recovered.id,
            recovered_job_status=recovered.status,
            recovered_job_attempts=recovered.attempts,
            dead_letter_job_id=dead_letter.id,
            dead_letter_job_status=dead_letter.status,
            dead_letter_count=len(dead_letters),
        )
    finally:
        cleanup_verification_keys(config, client)


def verify_ack_lifecycle(
    config: RedisQueueVerificationConfig,
    queue: RedisJobQueue,
):
    submitted = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            user_id=config.user_id,
            run_id=f"run_redis_ack_{uuid4().hex[:12]}",
            requested_by_user_id=config.user_id,
        ),
        max_attempts=1,
    )
    claimed = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id=config.worker_id,
        lease_seconds=config.lease_seconds,
    )
    if claimed is None:
        raise RuntimeError("Redis queue did not return the submitted job")
    if claimed.id != submitted.id:
        raise RuntimeError("Redis queue claimed a different job than the submitted job")
    if claimed.status != JobStatus.RUNNING:
        raise RuntimeError("Redis queue did not mark the claimed job as running")
    if claimed.worker_id != config.worker_id:
        raise RuntimeError("Redis queue did not record the claiming worker")
    acknowledged = queue.ack(claimed.id)
    if acknowledged.status != JobStatus.SUCCEEDED:
        raise RuntimeError("Redis queue did not acknowledge the claimed job")
    return acknowledged


def verify_expired_lease_recovery(
    config: RedisQueueVerificationConfig,
    queue: RedisJobQueue,
):
    now = utc_now()
    submitted = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            user_id=config.user_id,
            run_id=f"run_redis_recovered_{uuid4().hex[:12]}",
            requested_by_user_id=config.user_id,
        ),
        now=now,
        max_attempts=2,
    )
    claimed = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id=config.worker_id,
        now=now,
        lease_seconds=config.lease_seconds,
    )
    if claimed is None or claimed.id != submitted.id:
        raise RuntimeError("Redis queue did not return the lease recovery verification job")
    recovered = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id=f"{config.worker_id}_recovered",
        now=now + timedelta(seconds=config.lease_seconds),
        lease_seconds=config.lease_seconds,
    )
    if recovered is None:
        raise RuntimeError("Redis queue did not recover the expired lease")
    if recovered.id != submitted.id:
        raise RuntimeError("Redis queue recovered a different job than the expired lease job")
    if recovered.status != JobStatus.RUNNING:
        raise RuntimeError("Redis queue did not mark the recovered job as running")
    if recovered.attempts != 2:
        raise RuntimeError("Redis queue did not increment attempts after lease recovery")
    return recovered


def verify_dead_letter_lifecycle(
    config: RedisQueueVerificationConfig,
    queue: RedisJobQueue,
):
    submitted = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            user_id=config.user_id,
            run_id=f"run_redis_dead_letter_{uuid4().hex[:12]}",
            requested_by_user_id=config.user_id,
        ),
        max_attempts=1,
    )
    claimed = queue.claim(
        JobType.RUN_EXECUTION,
        worker_id=config.worker_id,
        lease_seconds=config.lease_seconds,
    )
    if claimed is None:
        raise RuntimeError("Redis queue did not return the dead-letter verification job")
    if claimed.id != submitted.id:
        raise RuntimeError("Redis queue claimed a different dead-letter verification job")
    rejected = queue.reject(
        claimed.id,
        "verification rejection",
        retry_delay_seconds=config.retry_delay_seconds,
    )
    if rejected.status != JobStatus.DEAD_LETTER:
        raise RuntimeError("Redis queue did not move the rejected job to dead-letter")
    return rejected


def cleanup_verification_keys(
    config: RedisQueueVerificationConfig,
    client=None,
) -> int:
    resolved_client = client or connect_redis(config.redis_url)
    keys = list(resolved_client.scan_iter(match=f"{config.key_prefix}:*"))
    if not keys:
        return 0
    return int(resolved_client.delete(*keys))


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_redis_queue(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
