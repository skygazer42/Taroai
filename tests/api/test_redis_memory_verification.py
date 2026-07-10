import fnmatch

import pytest
import redis

from taroai.memory.redis_verification import (
    RedisShortTermMemoryVerificationConfig,
    parse_args,
    verify_redis_short_term_memory,
)


class LocalRedisClient:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    def ping(self) -> bool:
        return True

    def set(self, name: str, value: str, ex: int | None = None) -> None:
        self.values[name] = value
        if ex is not None:
            self.expirations[name] = ex

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, *names: str) -> int:
        removed = 0
        for name in names:
            if name in self.values:
                removed += 1
            self.values.pop(name, None)
            self.expirations.pop(name, None)
        return removed

    def scan_iter(self, match: str):
        for name in sorted(self.values):
            if fnmatch.fnmatch(name, match):
                yield name

    def ttl(self, name: str) -> int:
        if name not in self.values:
            return -2
        return self.expirations.get(name, -1)


def test_redis_short_term_memory_verification_config_requires_redis_url():
    with pytest.raises(ValueError, match="Redis memory verification requires a Redis URL"):
        RedisShortTermMemoryVerificationConfig(redis_url="postgresql://localhost/taroai")


def test_redis_short_term_memory_verification_cli_parses_url_prefix_and_ttl():
    config = parse_args(
        [
            "--redis-url",
            "redis://localhost:6379/0",
            "--key-prefix",
            "taroai:verify:memory:ci",
            "--ttl-seconds",
            "120",
        ]
    )

    assert config.redis_url == "redis://localhost:6379/0"
    assert config.key_prefix == "taroai:verify:memory:ci"
    assert config.ttl_seconds == 120


def test_redis_short_term_memory_verification_exercises_ttl_scope_and_cleanup(monkeypatch):
    client = LocalRedisClient()
    monkeypatch.setattr(redis.Redis, "from_url", lambda url, decode_responses: client)
    config = RedisShortTermMemoryVerificationConfig(
        redis_url="redis://localhost:6379/0",
        key_prefix="taroai:verify:memory:unit",
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        ttl_seconds=90,
    )

    result = verify_redis_short_term_memory(config)

    assert result.ping_ok is True
    assert result.written_key == "planner.scratchpad"
    assert result.retrieved_value == {"next": "verify Redis short-term memory"}
    assert result.listed_keys == ["planner.scratchpad", "tool.last_result"]
    assert result.cross_tenant_visible is False
    assert result.deleted_key_visible is False
    assert result.deleted_for_tenant_count == 2
    assert result.ttl_seconds_remaining == 90
    assert client.values == {}
    assert client.expirations == {}
