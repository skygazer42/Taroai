import argparse
import json
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from taroai.memory.models import ShortTermMemoryWrite
from taroai.memory.service import RedisMemoryConfigurationError, RedisShortTermMemoryService


class RedisShortTermMemoryVerificationConfig(BaseModel):
    redis_url: str = Field(min_length=1)
    key_prefix: str = Field(default_factory=lambda: f"taroai:verify:memory:{uuid4().hex[:12]}")
    tenant_id: str = Field(default="tenant_memory_verify", min_length=1)
    workspace_id: str = Field(default="workspace_memory_verify", min_length=1)
    run_id: str = Field(default="run_memory_verify", min_length=1)
    ttl_seconds: int = Field(default=60, ge=2)

    @model_validator(mode="after")
    def validate_redis_url(self) -> "RedisShortTermMemoryVerificationConfig":
        scheme = urlparse(self.redis_url).scheme
        if scheme not in {"redis", "rediss", "unix"}:
            raise ValueError("Redis memory verification requires a Redis URL")
        return self


class RedisShortTermMemoryVerificationResult(BaseModel):
    key_prefix: str
    ping_ok: bool
    written_key: str
    retrieved_value: dict[str, Any]
    listed_keys: list[str] = Field(default_factory=list)
    cross_tenant_visible: bool
    deleted_key_visible: bool
    deleted_for_tenant_count: int
    ttl_seconds_remaining: int


def parse_args(argv: list[str] | None = None) -> RedisShortTermMemoryVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify Redis-backed short-term memory behavior."
    )
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--key-prefix", default=None)
    parser.add_argument("--ttl-seconds", type=int, default=60)
    parsed = parser.parse_args(argv)
    config_data = {
        "redis_url": parsed.redis_url,
        "ttl_seconds": parsed.ttl_seconds,
    }
    if parsed.key_prefix is not None:
        config_data["key_prefix"] = parsed.key_prefix
    return RedisShortTermMemoryVerificationConfig(**config_data)


def connect_redis(redis_url: str):
    try:
        import redis
    except ImportError as error:
        raise RedisMemoryConfigurationError(
            "redis package is required for RedisShortTermMemoryService"
        ) from error
    return redis.Redis.from_url(redis_url, decode_responses=True)


def verify_redis_short_term_memory(
    config: RedisShortTermMemoryVerificationConfig,
) -> RedisShortTermMemoryVerificationResult:
    client = connect_redis(config.redis_url)
    ping_ok = bool(client.ping())
    cleanup_verification_keys(config, client)
    service = RedisShortTermMemoryService(
        url=config.redis_url,
        key_prefix=config.key_prefix,
        client=client,
    )
    try:
        entry = service.put(
            ShortTermMemoryWrite(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                key="planner.scratchpad",
                value={"next": "verify Redis short-term memory"},
                ttl_seconds=config.ttl_seconds,
                created_by="user_memory_verify",
            )
        )
        service.put(
            ShortTermMemoryWrite(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                key="tool.last_result",
                value={"count": 3},
                ttl_seconds=config.ttl_seconds,
                created_by="user_memory_verify",
            )
        )
        other_tenant_id = f"{config.tenant_id}_other"
        service.put(
            ShortTermMemoryWrite(
                tenant_id=other_tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                key="other.tenant.marker",
                value={"next": "other tenant"},
                ttl_seconds=config.ttl_seconds,
                created_by="user_memory_verify",
            )
        )
        service.put(
            ShortTermMemoryWrite(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=f"{config.run_id}_cleanup",
                key="cleanup.marker",
                value={"cleanup": True},
                ttl_seconds=config.ttl_seconds,
                created_by="user_memory_verify",
            )
        )
        retrieved = service.get(config.tenant_id, config.run_id, entry.key)
        if retrieved is None:
            raise RuntimeError("Redis short-term memory entry was not readable after write")
        if retrieved.value != entry.value:
            raise RuntimeError("Redis short-term memory value changed after write")
        ttl_seconds_remaining = int(client.ttl(service._key(config.tenant_id, config.run_id, entry.key)))
        if ttl_seconds_remaining <= 0:
            raise RuntimeError("Redis short-term memory entry did not receive a TTL")
        listed_keys = [
            current_entry.key
            for current_entry in service.list_for_run(config.tenant_id, config.run_id)
        ]
        cross_tenant_visible = (
            service.get(config.tenant_id, config.run_id, "other.tenant.marker") is not None
        )
        if cross_tenant_visible:
            raise RuntimeError("Redis short-term memory allowed a cross-tenant read")
        service.delete(config.tenant_id, config.run_id, entry.key)
        deleted_key_visible = service.get(config.tenant_id, config.run_id, entry.key) is not None
        if deleted_key_visible:
            raise RuntimeError("Redis short-term memory key remained visible after delete")
        deleted_for_tenant_count = service.delete_for_tenant(config.tenant_id)
        return RedisShortTermMemoryVerificationResult(
            key_prefix=config.key_prefix,
            ping_ok=ping_ok,
            written_key=entry.key,
            retrieved_value=retrieved.value,
            listed_keys=listed_keys,
            cross_tenant_visible=cross_tenant_visible,
            deleted_key_visible=deleted_key_visible,
            deleted_for_tenant_count=deleted_for_tenant_count,
            ttl_seconds_remaining=ttl_seconds_remaining,
        )
    finally:
        cleanup_verification_keys(config, client)


def cleanup_verification_keys(
    config: RedisShortTermMemoryVerificationConfig,
    client=None,
) -> int:
    resolved_client = client or connect_redis(config.redis_url)
    keys = [
        redis_key
        for redis_key in resolved_client.scan_iter(match=f"{config.key_prefix}:*")
    ]
    if not keys:
        return 0
    return int(resolved_client.delete(*keys))


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_redis_short_term_memory(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
