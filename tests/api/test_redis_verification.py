import fnmatch
from pathlib import Path

import pytest
import redis

from taroai.workers.models import JobStatus
from taroai.workers.redis_verification import (
    RedisQueueVerificationConfig,
    parse_args,
    verify_redis_queue,
)


class LocalRedisClient:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

    def ping(self) -> bool:
        return True

    def hset(self, name: str, key: str, value: str) -> None:
        self.hashes.setdefault(name, {})[key] = value

    def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(key)

    def hvals(self, name: str) -> list[str]:
        return list(self.hashes.get(name, {}).values())

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

    def scan_iter(self, match: str):
        keys = list(self.hashes.keys()) + list(self.lists.keys())
        for key in keys:
            if fnmatch.fnmatch(key, match):
                yield key

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.hashes:
                del self.hashes[key]
                removed += 1
            if key in self.lists:
                del self.lists[key]
                removed += 1
        return removed


def test_redis_queue_verification_config_requires_redis_url():
    with pytest.raises(ValueError, match="Redis verification requires a Redis URL"):
        RedisQueueVerificationConfig(redis_url="postgresql://localhost/taroai")


def test_redis_queue_verification_cli_parses_url_and_key_prefix():
    config = parse_args(
        [
            "--redis-url",
            "redis://localhost:6379/0",
            "--key-prefix",
            "taroai:verify:ci",
        ]
    )

    assert config.redis_url == "redis://localhost:6379/0"
    assert config.key_prefix == "taroai:verify:ci"


def test_verify_redis_queue_script_wraps_python_cli():
    script = Path("scripts/verify-redis-queue.sh")

    text = script.read_text()

    assert "python -m taroai.workers.redis_verification" in text
    assert "--redis-url" in text
    assert "TAROAI_REDIS_URL" in text


def test_redis_queue_verification_exercises_ack_dead_letter_and_cleanup(monkeypatch):
    client = LocalRedisClient()
    monkeypatch.setattr(redis.Redis, "from_url", lambda url, decode_responses: client)
    config = RedisQueueVerificationConfig(
        redis_url="redis://localhost:6379/0",
        key_prefix="taroai:verify:unit",
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        user_id="user_verify",
        worker_id="worker_verify",
    )

    result = verify_redis_queue(config)

    assert result.ping_ok is True
    assert result.acknowledged_job_status == JobStatus.SUCCEEDED
    assert result.recovered_job_status == JobStatus.RUNNING
    assert result.recovered_job_attempts == 2
    assert result.dead_letter_job_status == JobStatus.DEAD_LETTER
    assert result.dead_letter_count == 1
    assert client.hashes == {}
    assert client.lists == {}
