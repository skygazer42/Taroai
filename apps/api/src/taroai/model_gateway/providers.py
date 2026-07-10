import json
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.db import DatabaseConfig, connect_database
from taroai.domain import utc_now
from taroai.model_gateway.models import (
    ModelGatewayError,
    ModelGatewayRequest,
    ModelUsage,
)


class ModelProviderRateLimitError(ModelGatewayError):
    def __init__(self, message: str, metadata: dict):
        super().__init__(message)
        self.metadata = metadata


class ModelProviderRateLimitStoreConfigurationError(ModelGatewayError):
    pass


class ModelProviderRateLimit(BaseModel):
    max_requests_per_minute: int = Field(default=0, ge=0)
    max_tokens_per_minute: int = Field(default=0, ge=0)

    def enabled(self) -> bool:
        return self.max_requests_per_minute > 0 or self.max_tokens_per_minute > 0


class ModelProviderFallbackPolicy(BaseModel):
    on_response_error: bool = True
    on_rate_limit: bool = True


MODEL_PROVIDER_CHAT_REQUEST_OPTION_RESERVED_KEYS = {
    "model",
    "messages",
    "stream",
    "tools",
    "tool_choice",
    "temperature",
    "max_tokens",
}


def validate_chat_request_options(options: dict[str, Any]) -> dict[str, Any]:
    reserved = sorted(
        key
        for key in options
        if key in MODEL_PROVIDER_CHAT_REQUEST_OPTION_RESERVED_KEYS
    )
    if reserved:
        raise ValueError(
            "chat_request_options cannot override core payload fields: "
            + ", ".join(reserved)
        )
    return options


class ModelProviderConfig(BaseModel):
    id: str = Field(min_length=1)
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1)
    api_key: str = Field(default="", exclude=True, repr=False)
    api_key_secret_ref_id: str | None = None
    secret_lease_ttl_seconds: int = Field(default=60, ge=1)
    default_model: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    tenant_id: str | None = None
    workspace_id: str | None = None
    priority: int = Field(default=100, ge=0)
    timeout_seconds: int = Field(default=30, ge=1)
    chat_request_options: dict[str, Any] = Field(default_factory=dict)
    rate_limit: ModelProviderRateLimit = Field(default_factory=ModelProviderRateLimit)
    fallback_enabled: bool = True
    fallback_policy: ModelProviderFallbackPolicy = Field(
        default_factory=ModelProviderFallbackPolicy
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_tenant_for_workspace(self):
        if self.workspace_id is not None and self.tenant_id is None:
            raise ValueError("tenant_id is required when workspace_id is set")
        validate_chat_request_options(self.chat_request_options)
        return self

    def matches(self, request: ModelGatewayRequest) -> bool:
        if self.tenant_id is not None and self.tenant_id != request.tenant_id:
            return False
        if self.workspace_id is not None and self.workspace_id != request.workspace_id:
            return False
        model = self.model_for_request(request)
        if self.model_ids and model is not None and model not in self.model_ids:
            return False
        return True

    def model_for_request(self, request: ModelGatewayRequest) -> str | None:
        return request.model or self.default_model

    def specificity(self, request: ModelGatewayRequest) -> int:
        score = 0
        if self.tenant_id is not None:
            score += 1
        if self.workspace_id is not None:
            score += 2
        if self.model_ids and self.model_for_request(request) in self.model_ids:
            score += 4
        return score

    def allows_fallback(
        self,
        reason: Literal["response_error", "rate_limit"],
    ) -> bool:
        if not self.fallback_enabled:
            return False
        if reason == "response_error":
            return self.fallback_policy.on_response_error
        if reason == "rate_limit":
            return self.fallback_policy.on_rate_limit
        return False


class ModelProviderRegistry(BaseModel):
    providers: list[ModelProviderConfig] = Field(default_factory=list)

    def candidates(self, request: ModelGatewayRequest) -> list[ModelProviderConfig]:
        return sorted(
            [provider for provider in self.providers if provider.matches(request)],
            key=lambda provider: (
                -provider.specificity(request),
                provider.priority,
                provider.id,
            ),
        )


class ModelProviderUsageSample(BaseModel):
    tenant_id: str | None = None
    provider_id: str
    request_count: int = Field(default=1, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class ModelProviderRateLimitReservation(BaseModel):
    tenant_id: str
    provider_id: str
    request_count: int = Field(default=1, ge=1)
    reserved_tokens: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class ModelProviderRateLimitStore(BaseModel):
    def list_recent_samples(
        self,
        tenant_id: str | None,
        provider_id: str,
        since: datetime,
    ) -> list[ModelProviderUsageSample]:
        raise NotImplementedError

    def record_sample(self, sample: ModelProviderUsageSample) -> None:
        raise NotImplementedError

    def prune_before(self, tenant_id: str | None, before: datetime) -> None:
        raise NotImplementedError

    def reserve_request(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest,
        window_seconds: int,
        now: datetime | None = None,
        reserved_tokens: int = 0,
    ) -> ModelProviderRateLimitReservation | None:
        return None


class InMemoryModelProviderRateLimitStore(ModelProviderRateLimitStore):
    samples: list[ModelProviderUsageSample] = Field(default_factory=list)

    def list_recent_samples(
        self,
        tenant_id: str | None,
        provider_id: str,
        since: datetime,
    ) -> list[ModelProviderUsageSample]:
        self.prune_before(tenant_id, since)
        return [
            sample
            for sample in self.samples
            if sample.tenant_id == tenant_id
            and sample.provider_id == provider_id
            and sample.created_at >= since
        ]

    def record_sample(self, sample: ModelProviderUsageSample) -> None:
        self.samples.append(sample)

    def prune_before(self, tenant_id: str | None, before: datetime) -> None:
        self.samples = [
            sample
            for sample in self.samples
            if sample.tenant_id != tenant_id or sample.created_at >= before
        ]


class SqlModelProviderRateLimitStore(ModelProviderRateLimitStore):
    config: DatabaseConfig

    def list_recent_samples(
        self,
        tenant_id: str | None,
        provider_id: str,
        since: datetime,
    ) -> list[ModelProviderUsageSample]:
        self._require_tenant(tenant_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT tenant_id, provider_id, request_count, total_tokens, created_at
                FROM model_provider_rate_limit_samples
                WHERE tenant_id = ? AND provider_id = ? AND created_at >= ?
                ORDER BY created_at
                """,
                (tenant_id, provider_id, self._dt(since)),
            ).fetchall()
        return [self._sample_from_row(row) for row in rows]

    def record_sample(self, sample: ModelProviderUsageSample) -> None:
        self._require_tenant(sample.tenant_id)
        with self._connect() as connection:
            self._ensure_tenant(connection, sample.tenant_id)
            connection.execute(
                """
                INSERT INTO model_provider_rate_limit_samples (
                    tenant_id, provider_id, request_count, total_tokens, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sample.tenant_id,
                    sample.provider_id,
                    sample.request_count,
                    sample.total_tokens,
                    self._dt(sample.created_at),
                ),
            )

    def prune_before(self, tenant_id: str | None, before: datetime) -> None:
        self._require_tenant(tenant_id)
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM model_provider_rate_limit_samples
                WHERE tenant_id = ? AND created_at < ?
                """,
                (tenant_id, self._dt(before)),
            )

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _require_tenant(self, tenant_id: str | None) -> None:
        if tenant_id is None:
            raise ValueError("tenant_id is required for SQL model provider rate limits")

    def _sample_from_row(self, row) -> ModelProviderUsageSample:
        return ModelProviderUsageSample(
            tenant_id=row["tenant_id"],
            provider_id=row["provider_id"],
            request_count=int(row["request_count"]),
            total_tokens=int(row["total_tokens"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


class RedisModelProviderRateLimitStore(ModelProviderRateLimitStore):
    url: str = Field(min_length=1)
    key_prefix: str = Field(
        default="taroai:model-provider-rate-limits",
        min_length=1,
    )
    client: Any | None = Field(default=None, exclude=True, repr=False)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def list_recent_samples(
        self,
        tenant_id: str | None,
        provider_id: str,
        since: datetime,
    ) -> list[ModelProviderUsageSample]:
        self._require_tenant(tenant_id)
        client = self._client()
        values = client.zrangebyscore(
            self._samples_key(tenant_id, provider_id),
            self._score(since),
            "+inf",
        )
        return [self._sample_from_member(value) for value in values]

    def record_sample(self, sample: ModelProviderUsageSample) -> None:
        self._require_tenant(sample.tenant_id)
        client = self._client()
        client.sadd(self._tenant_providers_key(sample.tenant_id), sample.provider_id)
        client.zadd(
            self._samples_key(sample.tenant_id, sample.provider_id),
            {self._sample_member(sample): self._score(sample.created_at)},
        )

    def prune_before(self, tenant_id: str | None, before: datetime) -> None:
        self._require_tenant(tenant_id)
        client = self._client()
        for raw_provider_id in client.smembers(self._tenant_providers_key(tenant_id)):
            provider_id = self._decode(raw_provider_id)
            client.zremrangebyscore(
                self._samples_key(tenant_id, provider_id),
                "-inf",
                self._score(before),
            )

    def reserve_request(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest,
        window_seconds: int,
        now: datetime | None = None,
        reserved_tokens: int = 0,
    ) -> ModelProviderRateLimitReservation | None:
        if (
            provider.rate_limit.max_requests_per_minute <= 0
            and provider.rate_limit.max_tokens_per_minute <= 0
        ):
            return None
        tenant_id = request.tenant_id
        self._require_tenant(tenant_id)
        current_time = now or utc_now()
        cutoff = current_time - timedelta(seconds=window_seconds)
        reservation_sample = ModelProviderUsageSample(
            tenant_id=tenant_id,
            provider_id=provider.id,
            request_count=1,
            total_tokens=reserved_tokens,
            created_at=current_time,
        )
        result = self._reserve_request_with_script(
            provider=provider,
            tenant_id=tenant_id,
            cutoff=cutoff,
            current_time=current_time,
            sample=reservation_sample,
        )
        if not bool(result.get("allowed")):
            raise ModelProviderRateLimitError(
                "model provider rate limit exceeded",
                metadata={
                    "provider_id": provider.id,
                    "limit_type": str(result["limit_type"]),
                    "current_quantity": int(result["current_quantity"]),
                    "requested_quantity": int(result.get("requested_quantity") or 0),
                    "limit": int(result["limit"]),
                    "window_seconds": window_seconds,
                },
            )
        return ModelProviderRateLimitReservation(
            tenant_id=tenant_id,
            provider_id=provider.id,
            request_count=1,
            reserved_tokens=reserved_tokens,
            created_at=current_time,
        )

    def _client(self):
        if self.client is not None:
            return self.client
        try:
            import redis
        except ImportError as error:
            raise ModelProviderRateLimitStoreConfigurationError(
                "redis package is required for RedisModelProviderRateLimitStore"
            ) from error
        client = redis.Redis.from_url(self.url, decode_responses=True)
        object.__setattr__(self, "client", client)
        return client

    def _require_tenant(self, tenant_id: str | None) -> None:
        if tenant_id is None:
            raise ValueError("tenant_id is required for Redis model provider rate limits")

    def _tenant_providers_key(self, tenant_id: str) -> str:
        return f"{self.key_prefix}:{self._key_part(tenant_id)}:providers"

    def _samples_key(self, tenant_id: str, provider_id: str) -> str:
        return (
            f"{self.key_prefix}:{self._key_part(tenant_id)}:"
            f"{self._key_part(provider_id)}:samples"
        )

    def _key_part(self, value: str) -> str:
        return quote(value, safe="")

    def _sample_member(self, sample: ModelProviderUsageSample) -> str:
        return json.dumps(
            {
                "sample": sample.model_dump(mode="json"),
                "nonce": uuid4().hex,
            },
            sort_keys=True,
        )

    def _sample_from_member(self, value: str | bytes) -> ModelProviderUsageSample:
        payload = json.loads(self._decode(value))
        sample = payload["sample"] if isinstance(payload, dict) and "sample" in payload else payload
        return ModelProviderUsageSample.model_validate(sample)

    def _reserve_request_with_script(
        self,
        provider: ModelProviderConfig,
        tenant_id: str,
        cutoff: datetime,
        current_time: datetime,
        sample: ModelProviderUsageSample,
    ) -> dict[str, Any]:
        raw_result = self._client().eval(
            self._reserve_request_script(),
            2,
            self._samples_key(tenant_id, provider.id),
            self._tenant_providers_key(tenant_id),
            provider.id,
            self._score(cutoff),
            self._score(current_time),
            provider.rate_limit.max_requests_per_minute,
            provider.rate_limit.max_tokens_per_minute,
            sample.total_tokens,
            self._sample_member(sample),
        )
        return json.loads(self._decode(raw_result))

    def _reserve_request_script(self) -> str:
        return """
local samples_key = KEYS[1]
local providers_key = KEYS[2]
local provider_id = ARGV[1]
local cutoff = tonumber(ARGV[2])
local now_score = tonumber(ARGV[3])
local request_limit = tonumber(ARGV[4])
local token_limit = tonumber(ARGV[5])
local reserved_tokens = tonumber(ARGV[6])
local member = ARGV[7]

redis.call("SADD", providers_key, provider_id)
redis.call("ZREMRANGEBYSCORE", samples_key, "-inf", cutoff)

local request_count = 0
local token_count = 0
local members = redis.call("ZRANGEBYSCORE", samples_key, cutoff, "+inf")
for _, raw in ipairs(members) do
  local ok, payload = pcall(cjson.decode, raw)
  if ok and payload then
    local sample = payload["sample"] or payload
    request_count = request_count + tonumber(sample["request_count"] or 0)
    token_count = token_count + tonumber(sample["total_tokens"] or 0)
  end
end

if request_limit > 0 and request_count >= request_limit then
  return cjson.encode({
    allowed = false,
    limit_type = "requests_per_minute",
    current_quantity = request_count,
    limit = request_limit
  })
end

if token_limit > 0 and (token_count + reserved_tokens) > token_limit then
  return cjson.encode({
    allowed = false,
    limit_type = "tokens_per_minute",
    current_quantity = token_count,
    requested_quantity = reserved_tokens,
    limit = token_limit
  })
end

redis.call("ZADD", samples_key, now_score, member)
return cjson.encode({
  allowed = true,
  current_quantity = request_count + 1
})
"""

    def _score(self, value: datetime) -> float:
        return value.timestamp()

    def _decode(self, value: str | bytes) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value


class ModelProviderRateLimiter(BaseModel):
    samples: list[ModelProviderUsageSample] = Field(default_factory=list)
    store: ModelProviderRateLimitStore | None = None
    window_seconds: int = Field(default=60, ge=1)

    def assert_allowed(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest | None = None,
        now: datetime | None = None,
    ) -> None:
        if not provider.rate_limit.enabled():
            return
        current_time = now or utc_now()
        provider_samples = self._recent_samples(provider, request, current_time)
        request_count = sum(sample.request_count for sample in provider_samples)
        token_count = sum(sample.total_tokens for sample in provider_samples)
        if (
            provider.rate_limit.max_requests_per_minute > 0
            and request_count >= provider.rate_limit.max_requests_per_minute
        ):
            raise self._rate_limited_error(
                provider=provider,
                limit_type="requests_per_minute",
                current_quantity=request_count,
                limit=provider.rate_limit.max_requests_per_minute,
            )
        if (
            provider.rate_limit.max_tokens_per_minute > 0
            and token_count >= provider.rate_limit.max_tokens_per_minute
        ):
            raise self._rate_limited_error(
                provider=provider,
                limit_type="tokens_per_minute",
                current_quantity=token_count,
                limit=provider.rate_limit.max_tokens_per_minute,
            )

    def reserve(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest,
        now: datetime | None = None,
    ) -> ModelProviderRateLimitReservation | None:
        if not provider.rate_limit.enabled():
            return None
        if self.store is not None:
            reserved_tokens = self._reserved_tokens_for_request(provider, request)
            reservation = self.store.reserve_request(
                provider=provider,
                request=request,
                window_seconds=self.window_seconds,
                now=now,
                reserved_tokens=reserved_tokens,
            )
            if reservation is not None:
                return reservation
        self.assert_allowed(provider, request, now=now)
        return None

    def record_success(
        self,
        provider_id: str,
        usage: ModelUsage | None,
        request: ModelGatewayRequest | None = None,
        now: datetime | None = None,
        reservation: ModelProviderRateLimitReservation | None = None,
    ) -> None:
        current_time = now or utc_now()
        total_tokens = usage.total_tokens if usage is not None else 0
        if reservation is not None:
            total_tokens = max(total_tokens - reservation.reserved_tokens, 0)
            if total_tokens <= 0:
                return
        sample = (
            ModelProviderUsageSample(
                tenant_id=self._sample_tenant_id(request),
                provider_id=provider_id,
                request_count=0 if reservation is not None else 1,
                total_tokens=total_tokens,
                created_at=current_time,
            )
        )
        if self.store is not None:
            self.store.prune_before(
                sample.tenant_id,
                current_time - timedelta(seconds=self.window_seconds),
            )
            self.store.record_sample(sample)
            return
        self._prune(current_time)
        self.samples.append(sample)

    def _recent_samples(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest | None,
        now: datetime,
    ) -> list[ModelProviderUsageSample]:
        since = now - timedelta(seconds=self.window_seconds)
        tenant_id = self._sample_tenant_id(request)
        if self.store is not None:
            self.store.prune_before(tenant_id, since)
            return self.store.list_recent_samples(tenant_id, provider.id, since)
        self._prune(now)
        return [
            sample
            for sample in self.samples
            if sample.provider_id == provider.id
            and (sample.tenant_id == tenant_id or sample.tenant_id is None)
        ]

    def _prune(self, now: datetime) -> None:
        earliest = now - timedelta(seconds=self.window_seconds)
        self.samples = [sample for sample in self.samples if sample.created_at >= earliest]

    def _sample_tenant_id(self, request: ModelGatewayRequest | None) -> str | None:
        if request is None:
            return None
        return request.tenant_id

    def _reserved_tokens_for_request(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest,
    ) -> int:
        if provider.rate_limit.max_tokens_per_minute <= 0:
            return 0
        return max(int(request.max_output_tokens or 0), 0)

    def _rate_limited_error(
        self,
        provider: ModelProviderConfig,
        limit_type: str,
        current_quantity: int,
        limit: int,
    ) -> ModelProviderRateLimitError:
        return ModelProviderRateLimitError(
            "model provider rate limit exceeded",
            metadata={
                "provider_id": provider.id,
                "limit_type": limit_type,
                "current_quantity": current_quantity,
                "limit": limit,
                "window_seconds": self.window_seconds,
            },
        )
