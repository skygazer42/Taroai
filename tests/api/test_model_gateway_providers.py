import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import Field

from taroai.agent import AgentRuntime
from taroai.app import create_app
from taroai.config import Settings
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.domain import RunCreate, RunStatus
from taroai.domain import utc_now
from taroai.model_gateway import (
    InMemoryModelProviderStore,
    ModelGateway,
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelGatewayResponseError,
    ModelMessage,
    ModelPolicy,
    ModelPolicyScope,
    ModelProviderConfig,
    ModelProviderChangeRequestCreate,
    ModelProviderFallbackPolicy,
    ModelProviderRateLimit,
    ModelProviderRateLimitError,
    ModelProviderRateLimiter,
    ModelProviderRegistry,
    ModelGatewayRouter,
    ModelProviderUpsert,
    ModelProviderUsageSample,
    ModelUsage,
    OpenAICompatibleModelGateway,
    PlannedToolCall,
    RedisModelProviderRateLimitStore,
    SqlModelProviderRateLimitStore,
    SqlModelProviderStore,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import InMemoryJobQueue
from taroai.workers.runner import build_agent_worker_runner
from tests.api.adapters import DeterministicToolGateway


def create_model_request(
    workspace_id: str = "workspace_sales",
    model: str | None = "gpt-enterprise",
    run_id: str = "run_1",
    max_output_tokens: int | None = None,
) -> ModelGatewayRequest:
    return ModelGatewayRequest(
        tenant_id="tenant_acme",
        workspace_id=workspace_id,
        user_id="user_1",
        run_id=run_id,
        model=model,
        messages=[ModelMessage(role="user", content="Plan governed work.")],
        max_output_tokens=max_output_tokens,
    )


def test_model_provider_registry_filters_explicit_provider_and_reasoning_capability():
    registry = ModelProviderRegistry(
        providers=[
            ModelProviderConfig(
                id="deepseek",
                display_name="DeepSeek",
                default_model="deepseek-chat",
                model_ids=["deepseek-chat"],
                reasoning_efforts=["low", "medium", "high"],
                default_reasoning_effort="medium",
            ),
            ModelProviderConfig(
                id="fallback",
                default_model="deepseek-chat",
                model_ids=["deepseek-chat"],
                reasoning_efforts=["low"],
            ),
        ]
    )

    supported = create_model_request(model="deepseek-chat").model_copy(
        update={"provider_id": "deepseek", "reasoning_effort": "medium"}
    )
    unsupported = supported.model_copy(update={"reasoning_effort": "minimal"})

    assert [provider.id for provider in registry.candidates(supported)] == ["deepseek"]
    assert registry.candidates(unsupported) == []


class RecordingModelGatewayRouter(ModelGatewayRouter):
    failing_provider_ids: set[str] = Field(default_factory=set, exclude=True)
    calls: list[tuple[str, str | None]] = Field(default_factory=list, exclude=True)

    def _call_provider(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest,
    ) -> ModelGatewayResponse:
        self.calls.append((provider.id, request.model))
        if provider.id in self.failing_provider_ids:
            raise ModelGatewayResponseError(f"{provider.id} unavailable")
        return ModelGatewayResponse(
            id=f"response_{provider.id}",
            provider=provider.id,
            model=request.model,
            output_text=json.dumps(
                {
                    "steps": [
                        {
                            "id": "step_route",
                            "title": "Route through provider",
                            "tool_name": "planning.record",
                            "tool_input": {"provider_id": provider.id},
                        }
                    ]
                }
            ),
            planned_steps=[
                PlannedToolCall(
                    id="step_route",
                    title="Route through provider",
                    tool_name="planning.record",
                    tool_input={"provider_id": provider.id},
                )
            ],
            usage=ModelUsage(input_tokens=4, output_tokens=6, total_tokens=10),
        )


class RecordingModelGateway(ModelGateway):
    requests: list[ModelGatewayRequest] = Field(default_factory=list)

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        self.requests.append(request)
        return ModelGatewayResponse(
            id=f"response_{request.run_id}",
            model=request.model,
            planned_steps=[
                PlannedToolCall(
                    id="step_policy_model",
                    title="Use policy-selected model",
                    tool_name="planning.record",
                    tool_input={"model": request.model},
                )
            ],
        )


class LocalRedisRateLimitClient:
    def __init__(self):
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.sets: dict[str, set[str]] = {}

    def zadd(self, name: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(name, {}).update(mapping)

    def zrangebyscore(self, name: str, minimum, maximum) -> list[str]:
        minimum_score = self._score(minimum)
        maximum_score = self._score(maximum)
        values = self.sorted_sets.get(name, {})
        return [
            member
            for member, _score in sorted(values.items(), key=lambda item: item[1])
            if minimum_score <= _score <= maximum_score
        ]

    def zremrangebyscore(self, name: str, minimum, maximum) -> int:
        minimum_score = self._score(minimum)
        maximum_score = self._score(maximum)
        values = self.sorted_sets.setdefault(name, {})
        removed = [
            member
            for member, _score in values.items()
            if minimum_score <= _score <= maximum_score
        ]
        for member in removed:
            del values[member]
        return len(removed)

    def sadd(self, name: str, value: str) -> None:
        self.sets.setdefault(name, set()).add(value)

    def smembers(self, name: str) -> set[str]:
        return set(self.sets.get(name, set()))

    def eval(self, _script: str, numkeys: int, *keys_and_args):
        assert numkeys == 2
        samples_key = keys_and_args[0]
        providers_key = keys_and_args[1]
        provider_id = keys_and_args[2]
        cutoff = float(keys_and_args[3])
        now_score = float(keys_and_args[4])
        request_limit = int(keys_and_args[5])
        token_limit = int(keys_and_args[6])
        reserved_tokens = int(keys_and_args[7])
        member = keys_and_args[8]

        self.sadd(providers_key, provider_id)
        self.zremrangebyscore(samples_key, "-inf", cutoff)
        samples = [
            self._sample_from_member(value)
            for value in self.zrangebyscore(samples_key, cutoff, "+inf")
        ]
        request_count = sum(sample["request_count"] for sample in samples)
        token_count = sum(sample["total_tokens"] for sample in samples)
        if request_limit > 0 and request_count >= request_limit:
            return json.dumps(
                {
                    "allowed": False,
                    "limit_type": "requests_per_minute",
                    "current_quantity": request_count,
                    "limit": request_limit,
                }
            )
        if token_limit > 0 and token_count + reserved_tokens > token_limit:
            return json.dumps(
                {
                    "allowed": False,
                    "limit_type": "tokens_per_minute",
                    "current_quantity": token_count,
                    "requested_quantity": reserved_tokens,
                    "limit": token_limit,
                }
            )
        self.zadd(samples_key, {member: now_score})
        return json.dumps(
            {
                "allowed": True,
                "current_quantity": request_count + 1,
            }
        )

    def _score(self, value) -> float:
        if value in {"+inf", "inf"}:
            return float("inf")
        if value in {"-inf"}:
            return float("-inf")
        return float(value)

    def _sample_from_member(self, value: str) -> dict:
        payload = json.loads(value)
        if isinstance(payload, dict) and "sample" in payload:
            return payload["sample"]
        return payload


def test_model_provider_registry_orders_workspace_specific_provider_first():
    registry = ModelProviderRegistry(
        providers=[
            ModelProviderConfig(
                id="global-openai",
                base_url="https://api.openai.com/v1",
                model_ids=["gpt-enterprise"],
                priority=100,
            ),
            ModelProviderConfig(
                id="tenant-openai",
                tenant_id="tenant_acme",
                base_url="https://tenant-model.example.com/v1",
                model_ids=["gpt-enterprise"],
                priority=20,
            ),
            ModelProviderConfig(
                id="workspace-openai",
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                base_url="https://sales-model.example.com/v1",
                model_ids=["gpt-enterprise"],
                priority=50,
            ),
        ]
    )

    candidates = registry.candidates(create_model_request())

    assert [candidate.id for candidate in candidates] == [
        "workspace-openai",
        "tenant-openai",
        "global-openai",
    ]


def test_model_gateway_router_falls_back_when_primary_provider_is_unavailable():
    router = RecordingModelGatewayRouter(
        provider_registry=ModelProviderRegistry(
            providers=[
                ModelProviderConfig(
                    id="primary",
                    base_url="https://primary-model.example.com/v1",
                    model_ids=["gpt-enterprise"],
                    priority=10,
                ),
                ModelProviderConfig(
                    id="secondary",
                    base_url="https://secondary-model.example.com/v1",
                    model_ids=["gpt-enterprise"],
                    priority=20,
                ),
            ]
        ),
        failing_provider_ids={"primary"},
    )

    response = router.create_plan(create_model_request())

    assert response.provider == "secondary"
    assert response.model == "gpt-enterprise"
    assert router.calls == [
        ("primary", "gpt-enterprise"),
        ("secondary", "gpt-enterprise"),
    ]
    assert [attempt.model_dump(mode="json") for attempt in response.provider_attempts] == [
        {
            "provider_id": "primary",
            "model": "gpt-enterprise",
            "status": "response_error",
            "invoked": True,
            "fallback_allowed": True,
            "error_type": "ModelGatewayResponseError",
        },
        {
            "provider_id": "secondary",
            "model": "gpt-enterprise",
            "status": "succeeded",
            "invoked": True,
            "fallback_allowed": False,
            "error_type": None,
        },
    ]


def test_model_gateway_router_respects_response_error_fallback_policy():
    router = RecordingModelGatewayRouter(
        provider_registry=ModelProviderRegistry(
            providers=[
                ModelProviderConfig(
                    id="primary",
                    base_url="https://primary-model.example.com/v1",
                    model_ids=["gpt-enterprise"],
                    priority=10,
                    fallback_policy=ModelProviderFallbackPolicy(
                        on_response_error=False,
                    ),
                ),
                ModelProviderConfig(
                    id="secondary",
                    base_url="https://secondary-model.example.com/v1",
                    model_ids=["gpt-enterprise"],
                    priority=20,
                ),
            ]
        ),
        failing_provider_ids={"primary"},
    )

    with pytest.raises(ModelGatewayResponseError):
        router.create_plan(create_model_request())

    assert router.calls == [("primary", "gpt-enterprise")]


def test_model_gateway_router_passes_provider_chat_request_options(monkeypatch):
    payloads = []

    def record_provider_call(self, payload, api_key):
        payloads.append(payload)
        return {
            "id": "response_deepseek",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "steps": [
                                    {
                                        "id": "step_route",
                                        "title": "Route through provider",
                                        "tool_name": "planning.record",
                                        "tool_input": {"provider_id": "deepseek"},
                                    }
                                ]
                            }
                        )
                    }
                }
            ],
        }

    monkeypatch.setattr(
        OpenAICompatibleModelGateway,
        "_post_chat_completions",
        record_provider_call,
    )
    router = ModelGatewayRouter(
        provider_registry=ModelProviderRegistry(
            providers=[
                ModelProviderConfig(
                    id="deepseek",
                    base_url="https://api.deepseek.com",
                    api_key="sk-provider",
                    default_model="deepseek-v4-flash",
                    chat_request_options={
                        "response_format": {"type": "json_object"},
                        "thinking": {"type": "disabled"},
                    },
                )
            ]
        )
    )

    response = router.create_plan(create_model_request(model=None))

    assert response.provider == "deepseek"
    assert payloads[0]["model"] == "deepseek-v4-flash"
    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert payloads[0]["thinking"] == {"type": "disabled"}


def test_model_gateway_router_skips_rate_limited_provider_before_invocation():
    router = RecordingModelGatewayRouter(
        provider_registry=ModelProviderRegistry(
            providers=[
                ModelProviderConfig(
                    id="primary",
                    base_url="https://primary-model.example.com/v1",
                    model_ids=["gpt-enterprise"],
                    priority=10,
                    rate_limit=ModelProviderRateLimit(max_requests_per_minute=1),
                ),
                ModelProviderConfig(
                    id="secondary",
                    base_url="https://secondary-model.example.com/v1",
                    model_ids=["gpt-enterprise"],
                    priority=20,
                ),
            ]
        )
    )
    router.rate_limiter.record_success(
        provider_id="primary",
        usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )

    response = router.create_plan(create_model_request())

    assert response.provider == "secondary"
    assert router.calls == [("secondary", "gpt-enterprise")]
    assert [attempt.model_dump(mode="json") for attempt in response.provider_attempts] == [
        {
            "provider_id": "primary",
            "model": "gpt-enterprise",
            "status": "rate_limited",
            "invoked": False,
            "fallback_allowed": True,
            "error_type": "ModelProviderRateLimitError",
        },
        {
            "provider_id": "secondary",
            "model": "gpt-enterprise",
            "status": "succeeded",
            "invoked": True,
            "fallback_allowed": False,
            "error_type": None,
        },
    ]


def test_model_gateway_router_respects_rate_limit_fallback_policy():
    router = RecordingModelGatewayRouter(
        provider_registry=ModelProviderRegistry(
            providers=[
                ModelProviderConfig(
                    id="primary",
                    base_url="https://primary-model.example.com/v1",
                    model_ids=["gpt-enterprise"],
                    priority=10,
                    rate_limit=ModelProviderRateLimit(max_requests_per_minute=1),
                    fallback_policy=ModelProviderFallbackPolicy(
                        on_rate_limit=False,
                    ),
                ),
                ModelProviderConfig(
                    id="secondary",
                    base_url="https://secondary-model.example.com/v1",
                    model_ids=["gpt-enterprise"],
                    priority=20,
                ),
            ]
        )
    )
    router.rate_limiter.record_success(
        provider_id="primary",
        usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )

    with pytest.raises(ModelProviderRateLimitError):
        router.create_plan(create_model_request())

    assert router.calls == []


def test_model_gateway_router_shares_sql_rate_limit_counters_across_instances(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    shared_store = SqlModelProviderRateLimitStore(
        config=DatabaseConfig(url=database_url)
    )
    registry = ModelProviderRegistry(
        providers=[
            ModelProviderConfig(
                id="primary",
                base_url="https://primary-model.example.com/v1",
                model_ids=["gpt-enterprise"],
                priority=10,
                rate_limit=ModelProviderRateLimit(max_requests_per_minute=1),
            ),
            ModelProviderConfig(
                id="secondary",
                base_url="https://secondary-model.example.com/v1",
                model_ids=["gpt-enterprise"],
                priority=20,
            ),
        ]
    )
    first_router = RecordingModelGatewayRouter(
        provider_registry=registry,
        rate_limiter=ModelProviderRateLimiter(store=shared_store),
    )
    second_router = RecordingModelGatewayRouter(
        provider_registry=registry,
        rate_limiter=ModelProviderRateLimiter(store=shared_store),
    )

    first_response = first_router.create_plan(create_model_request(run_id="run_first"))
    second_response = second_router.create_plan(create_model_request(run_id="run_second"))

    assert first_response.provider == "primary"
    assert first_router.calls == [("primary", "gpt-enterprise")]
    assert second_response.provider == "secondary"
    assert second_router.calls == [("secondary", "gpt-enterprise")]


def test_model_gateway_router_shares_redis_rate_limit_counters_across_instances():
    client = LocalRedisRateLimitClient()
    shared_store = RedisModelProviderRateLimitStore(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:model-provider-rate-limits",
        client=client,
    )
    registry = ModelProviderRegistry(
        providers=[
            ModelProviderConfig(
                id="primary",
                base_url="https://primary-model.example.com/v1",
                model_ids=["gpt-enterprise"],
                priority=10,
                rate_limit=ModelProviderRateLimit(max_requests_per_minute=1),
            ),
            ModelProviderConfig(
                id="secondary",
                base_url="https://secondary-model.example.com/v1",
                model_ids=["gpt-enterprise"],
                priority=20,
            ),
        ]
    )
    first_router = RecordingModelGatewayRouter(
        provider_registry=registry,
        rate_limiter=ModelProviderRateLimiter(store=shared_store),
    )
    second_router = RecordingModelGatewayRouter(
        provider_registry=registry,
        rate_limiter=ModelProviderRateLimiter(store=shared_store),
    )

    first_response = first_router.create_plan(create_model_request(run_id="run_first"))
    second_response = second_router.create_plan(create_model_request(run_id="run_second"))

    assert first_response.provider == "primary"
    assert first_router.calls == [("primary", "gpt-enterprise")]
    assert second_response.provider == "secondary"
    assert second_router.calls == [("secondary", "gpt-enterprise")]
    assert second_response.provider_attempts[0].status == "rate_limited"


def test_redis_model_provider_rate_limiter_reserves_request_before_success_record():
    shared_store = RedisModelProviderRateLimitStore(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:model-provider-rate-limits",
        client=LocalRedisRateLimitClient(),
    )
    provider = ModelProviderConfig(
        id="primary",
        base_url="https://primary-model.example.com/v1",
        model_ids=["gpt-enterprise"],
        rate_limit=ModelProviderRateLimit(max_requests_per_minute=1),
    )
    first_limiter = ModelProviderRateLimiter(store=shared_store)
    second_limiter = ModelProviderRateLimiter(store=shared_store)

    first_limiter.reserve(provider, create_model_request(run_id="run_first"))

    with pytest.raises(ModelProviderRateLimitError) as error:
        second_limiter.reserve(provider, create_model_request(run_id="run_second"))

    assert error.value.metadata["provider_id"] == "primary"
    assert error.value.metadata["limit_type"] == "requests_per_minute"
    assert error.value.metadata["current_quantity"] == 1
    assert error.value.metadata["limit"] == 1


def test_redis_model_provider_rate_limiter_records_tokens_without_double_counting_request():
    now = utc_now()
    shared_store = RedisModelProviderRateLimitStore(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:model-provider-rate-limits",
        client=LocalRedisRateLimitClient(),
    )
    provider = ModelProviderConfig(
        id="primary",
        base_url="https://primary-model.example.com/v1",
        model_ids=["gpt-enterprise"],
        rate_limit=ModelProviderRateLimit(
            max_requests_per_minute=2,
            max_tokens_per_minute=100,
        ),
    )
    limiter = ModelProviderRateLimiter(store=shared_store)
    request = create_model_request(run_id="run_reserved")

    reservation = limiter.reserve(provider, request, now=now)
    limiter.record_success(
        provider_id=provider.id,
        usage=ModelUsage(input_tokens=10, output_tokens=15, total_tokens=25),
        request=request,
        now=now,
        reservation=reservation,
    )

    samples = shared_store.list_recent_samples(
        tenant_id="tenant_acme",
        provider_id="primary",
        since=now - timedelta(seconds=60),
    )
    assert sum(sample.request_count for sample in samples) == 1
    assert sum(sample.total_tokens for sample in samples) == 25


def test_redis_model_provider_rate_limiter_reserves_tokens_before_success_record():
    now = utc_now()
    shared_store = RedisModelProviderRateLimitStore(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:model-provider-rate-limits",
        client=LocalRedisRateLimitClient(),
    )
    provider = ModelProviderConfig(
        id="primary",
        base_url="https://primary-model.example.com/v1",
        model_ids=["gpt-enterprise"],
        rate_limit=ModelProviderRateLimit(
            max_requests_per_minute=10,
            max_tokens_per_minute=100,
        ),
    )
    first_limiter = ModelProviderRateLimiter(store=shared_store)
    second_limiter = ModelProviderRateLimiter(store=shared_store)

    reservation = first_limiter.reserve(
        provider,
        create_model_request(run_id="run_first", max_output_tokens=80),
        now=now,
    )

    assert reservation.reserved_tokens == 80
    with pytest.raises(ModelProviderRateLimitError) as error:
        second_limiter.reserve(
            provider,
            create_model_request(run_id="run_second", max_output_tokens=30),
            now=now,
        )

    assert error.value.metadata["provider_id"] == "primary"
    assert error.value.metadata["limit_type"] == "tokens_per_minute"
    assert error.value.metadata["current_quantity"] == 80
    assert error.value.metadata["requested_quantity"] == 30
    assert error.value.metadata["limit"] == 100


def test_redis_model_provider_rate_limiter_records_only_token_delta_after_reservation():
    now = utc_now()
    shared_store = RedisModelProviderRateLimitStore(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:model-provider-rate-limits",
        client=LocalRedisRateLimitClient(),
    )
    provider = ModelProviderConfig(
        id="primary",
        base_url="https://primary-model.example.com/v1",
        model_ids=["gpt-enterprise"],
        rate_limit=ModelProviderRateLimit(
            max_requests_per_minute=2,
            max_tokens_per_minute=100,
        ),
    )
    limiter = ModelProviderRateLimiter(store=shared_store)
    request = create_model_request(run_id="run_reserved", max_output_tokens=20)

    reservation = limiter.reserve(provider, request, now=now)
    limiter.record_success(
        provider_id=provider.id,
        usage=ModelUsage(input_tokens=15, output_tokens=10, total_tokens=25),
        request=request,
        now=now,
        reservation=reservation,
    )

    samples = shared_store.list_recent_samples(
        tenant_id="tenant_acme",
        provider_id="primary",
        since=now - timedelta(seconds=60),
    )
    assert sum(sample.request_count for sample in samples) == 1
    assert sum(sample.total_tokens for sample in samples) == 25


def test_redis_model_provider_rate_limit_store_prunes_expired_samples():
    client = LocalRedisRateLimitClient()
    store = RedisModelProviderRateLimitStore(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:model-provider-rate-limits",
        client=client,
    )
    now = utc_now()
    store.record_sample(
        ModelProviderUsageSample(
            tenant_id="tenant_acme",
            provider_id="primary",
            created_at=now - timedelta(seconds=120),
        )
    )
    store.record_sample(
        ModelProviderUsageSample(
            tenant_id="tenant_acme",
            provider_id="primary",
            total_tokens=42,
            created_at=now,
        )
    )

    store.prune_before("tenant_acme", now - timedelta(seconds=60))

    samples = store.list_recent_samples(
        tenant_id="tenant_acme",
        provider_id="primary",
        since=now - timedelta(seconds=60),
    )
    assert len(samples) == 1
    assert samples[0].total_tokens == 42


def test_app_wires_model_gateway_router_from_provider_settings():
    settings = Settings(
        model_gateway_providers=[
            ModelProviderConfig(
                id="enterprise-openai",
                base_url="https://model.example.com/v1",
                api_key_secret_ref_id="secret_model_key",
                default_model="gpt-enterprise",
                model_ids=["gpt-enterprise"],
            )
        ],
        _env_file=None,
    )

    app = create_app(settings=settings)

    gateway = app.state.runtime.model_gateway
    assert isinstance(gateway, ModelGatewayRouter)
    assert gateway.provider_registry.providers[0].id == "enterprise-openai"
    assert gateway.provider_registry.providers[0].api_key_secret_ref_id == "secret_model_key"
    assert gateway.secret_service is app.state.secret_service


def test_worker_runner_wires_model_gateway_router_from_provider_settings():
    settings = Settings(
        model_gateway_providers=[
            ModelProviderConfig(
                id="worker-openai",
                base_url="https://worker-model.example.com/v1",
                api_key_secret_ref_id="secret_worker_model_key",
                default_model="gpt-enterprise",
                model_ids=["gpt-enterprise"],
            )
        ],
        _env_file=None,
    )

    runner = build_agent_worker_runner(settings, queue=InMemoryJobQueue())

    gateway = runner.worker.runtime.model_gateway
    assert isinstance(gateway, ModelGatewayRouter)
    assert gateway.provider_registry.providers[0].id == "worker-openai"
    assert gateway.provider_registry.providers[0].api_key_secret_ref_id == "secret_worker_model_key"
    assert gateway.secret_service is not None


def test_app_and_worker_wire_sql_provider_rate_limit_store(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    provider = ModelProviderConfig(
        id="shared-openai",
        base_url="https://shared-model.example.com/v1",
        api_key_secret_ref_id="secret_shared_model_key",
        default_model="gpt-enterprise",
        model_ids=["gpt-enterprise"],
        rate_limit=ModelProviderRateLimit(max_requests_per_minute=10),
    )
    settings = Settings(
        database_url=database_url,
        model_gateway_providers=[provider],
        model_gateway_provider_rate_limit_backend="sql",
        _env_file=None,
    )

    app = create_app(settings=settings)
    runner = build_agent_worker_runner(settings, queue=InMemoryJobQueue())

    app_gateway = app.state.runtime.model_gateway
    worker_gateway = runner.worker.runtime.model_gateway
    assert isinstance(app_gateway, ModelGatewayRouter)
    assert isinstance(worker_gateway, ModelGatewayRouter)
    assert isinstance(
        app_gateway.rate_limiter.store,
        SqlModelProviderRateLimitStore,
    )
    assert isinstance(
        worker_gateway.rate_limiter.store,
        SqlModelProviderRateLimitStore,
    )


def test_app_and_worker_wire_redis_provider_rate_limit_store():
    provider = ModelProviderConfig(
        id="shared-openai",
        base_url="https://shared-model.example.com/v1",
        api_key_secret_ref_id="secret_shared_model_key",
        default_model="gpt-enterprise",
        model_ids=["gpt-enterprise"],
        rate_limit=ModelProviderRateLimit(max_requests_per_minute=10),
    )
    settings = Settings(
        model_gateway_providers=[provider],
        model_gateway_provider_rate_limit_backend="redis",
        redis_url="redis://redis.example.com:6379/0",
        _env_file=None,
    )

    app = create_app(settings=settings)
    runner = build_agent_worker_runner(settings, queue=InMemoryJobQueue())

    app_gateway = app.state.runtime.model_gateway
    worker_gateway = runner.worker.runtime.model_gateway
    assert isinstance(app_gateway, ModelGatewayRouter)
    assert isinstance(worker_gateway, ModelGatewayRouter)
    assert isinstance(
        app_gateway.rate_limiter.store,
        RedisModelProviderRateLimitStore,
    )
    assert isinstance(
        worker_gateway.rate_limiter.store,
        RedisModelProviderRateLimitStore,
    )
    assert app_gateway.rate_limiter.store.url == "redis://redis.example.com:6379/0"
    assert worker_gateway.rate_limiter.store.url == "redis://redis.example.com:6379/0"


def test_model_provider_store_persists_status_and_secret_reference(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlModelProviderStore(config=DatabaseConfig(url=database_url))

    created = store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="sales-openai",
            display_name="Sales OpenAI",
            base_url="https://sales-model.example.com/v1",
            api_key_secret_ref_id="secret_sales_model_key",
            default_model="gpt-enterprise",
            model_ids=["gpt-enterprise"],
            reasoning_efforts=["low", "medium", "high"],
            default_reasoning_effort="medium",
            workspace_id="workspace_sales",
            priority=5,
            timeout_seconds=17,
            fallback_enabled=False,
            fallback_policy=ModelProviderFallbackPolicy(
                on_response_error=False,
                on_rate_limit=True,
            ),
            rate_limit=ModelProviderRateLimit(
                max_requests_per_minute=60,
                max_tokens_per_minute=120000,
            ),
            updated_by_user_id="model_admin",
        )
    )
    store.set_status(
        tenant_id="tenant_acme",
        provider_id="sales-openai",
        status="disabled",
        updated_by_user_id="model_admin",
    )
    rotated = store.rotate_credential(
        tenant_id="tenant_acme",
        provider_id="sales-openai",
        api_key_secret_ref_id="secret_sales_model_key_v2",
        updated_by_user_id="model_admin",
    )

    restarted = SqlModelProviderStore(config=DatabaseConfig(url=database_url))
    records = restarted.list_providers("tenant_acme")

    assert created.status == "active"
    assert rotated.status == "disabled"
    assert len(records) == 1
    assert records[0].id == "sales-openai"
    assert records[0].tenant_id == "tenant_acme"
    assert records[0].status == "disabled"
    assert records[0].provider.api_key_secret_ref_id == "secret_sales_model_key_v2"
    assert records[0].provider.api_key == ""
    assert records[0].provider.display_name == "Sales OpenAI"
    assert records[0].provider.reasoning_efforts == ["low", "medium", "high"]
    assert records[0].provider.default_reasoning_effort == "medium"
    assert records[0].provider.workspace_id == "workspace_sales"
    assert records[0].provider.rate_limit.max_requests_per_minute == 60
    assert records[0].provider.fallback_enabled is False
    assert records[0].provider.fallback_policy.on_response_error is False
    assert records[0].provider.fallback_policy.on_rate_limit is True
    assert restarted.list_providers("tenant_other") == []


def test_model_provider_store_records_versions_and_rolls_back(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlModelProviderStore(config=DatabaseConfig(url=database_url))

    first = store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="sales-openai",
            base_url="https://sales-model.example.com/v1",
            api_key_secret_ref_id="secret_sales_model_key",
            default_model="gpt-enterprise",
            model_ids=["gpt-enterprise"],
            workspace_id="workspace_sales",
            priority=5,
            timeout_seconds=17,
            updated_by_user_id="model_admin",
        )
    )
    second = store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="sales-openai",
            base_url="https://sales-model.example.com/v1",
            api_key_secret_ref_id="secret_sales_model_key_v2",
            default_model="gpt-enterprise-v2",
            model_ids=["gpt-enterprise-v2"],
            workspace_id="workspace_sales",
            priority=2,
            timeout_seconds=19,
            updated_by_user_id="model_admin",
        )
    )

    versions = store.list_provider_versions("tenant_acme", "sales-openai")
    rolled_back = store.rollback_provider_version(
        tenant_id="tenant_acme",
        provider_id="sales-openai",
        version=1,
        updated_by_user_id="model_admin",
    )
    restarted = SqlModelProviderStore(config=DatabaseConfig(url=database_url))
    restarted_versions = restarted.list_provider_versions("tenant_acme", "sales-openai")

    assert first.current_version == 1
    assert second.current_version == 2
    assert [entry.version for entry in versions] == [1, 2]
    assert [entry.change_type for entry in versions] == ["upsert", "upsert"]
    assert versions[0].provider.default_model == "gpt-enterprise"
    assert versions[1].provider.default_model == "gpt-enterprise-v2"
    assert versions[0].provider.api_key == ""
    assert rolled_back.current_version == 3
    assert rolled_back.provider.default_model == "gpt-enterprise"
    assert rolled_back.provider.api_key_secret_ref_id == "secret_sales_model_key"
    assert rolled_back.status == "active"
    assert restarted.get_provider(
        "tenant_acme",
        "sales-openai",
    ).provider.default_model == "gpt-enterprise"
    assert [entry.version for entry in restarted_versions] == [1, 2, 3]
    assert restarted_versions[-1].change_type == "rollback"
    assert restarted_versions[-1].provider.default_model == "gpt-enterprise"
    assert restarted.list_provider_versions("tenant_other", "sales-openai") == []


def test_model_provider_store_applies_requested_change_only_after_approval(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlModelProviderStore(config=DatabaseConfig(url=database_url))

    request_record = store.create_provider_change_request(
        ModelProviderChangeRequestCreate(
            tenant_id="tenant_acme",
            provider_id="sales-openai",
            operation="upsert",
            provider_upsert=ModelProviderUpsert(
                tenant_id="tenant_acme",
                id="sales-openai",
                base_url="https://sales-model.example.com/v1",
                api_key_secret_ref_id="secret_sales_model_key",
                default_model="gpt-enterprise",
                model_ids=["gpt-enterprise"],
                workspace_id="workspace_sales",
                priority=5,
                timeout_seconds=17,
                updated_by_user_id="model_admin",
            ),
            requested_by_user_id="model_admin",
        )
    )
    restarted = SqlModelProviderStore(config=DatabaseConfig(url=database_url))

    requests_before_approval = restarted.list_provider_change_requests("tenant_acme")
    providers_before_approval = restarted.list_providers("tenant_acme")
    result = restarted.approve_provider_change_request(
        tenant_id="tenant_acme",
        request_id=request_record.id,
        reviewed_by_user_id="model_approver",
    )

    assert request_record.status == "pending"
    assert providers_before_approval == []
    assert len(requests_before_approval) == 1
    assert requests_before_approval[0].operation == "upsert"
    assert requests_before_approval[0].provider_id == "sales-openai"
    assert requests_before_approval[0].provider_upsert is not None
    assert requests_before_approval[0].provider_upsert.to_provider_config().api_key == ""
    assert result.change_request.status == "approved"
    assert result.change_request.reviewed_by_user_id == "model_approver"
    assert result.provider_record is not None
    assert result.provider_record.current_version == 1
    assert result.provider_record.provider.default_model == "gpt-enterprise"
    assert result.provider_record.provider.api_key_secret_ref_id == "secret_sales_model_key"
    assert restarted.list_provider_change_requests("tenant_other") == []


def test_model_gateway_router_loads_enabled_provider_store_records():
    store = InMemoryModelProviderStore()
    store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="tenant-openai",
            base_url="https://tenant-model.example.com/v1",
            api_key_secret_ref_id="secret_tenant_model_key",
            default_model="gpt-enterprise",
            model_ids=["gpt-enterprise"],
            updated_by_user_id="model_admin",
        )
    )
    store.set_status(
        tenant_id="tenant_acme",
        provider_id="tenant-openai",
        status="disabled",
        updated_by_user_id="model_admin",
    )
    settings = Settings(model_gateway_model="fallback-model", _env_file=None)

    disabled_app = create_app(settings=settings, model_provider_store=store)
    store.set_status(
        tenant_id="tenant_acme",
        provider_id="tenant-openai",
        status="active",
        updated_by_user_id="model_admin",
    )
    enabled_app = create_app(settings=settings, model_provider_store=store)

    assert not isinstance(disabled_app.state.runtime.model_gateway, ModelGatewayRouter)
    gateway = enabled_app.state.runtime.model_gateway
    assert isinstance(gateway, ModelGatewayRouter)
    assert gateway.provider_registry.providers[0].id == "tenant-openai"


def test_worker_runner_loads_enabled_sql_provider_store_records(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlModelProviderStore(config=DatabaseConfig(url=database_url))
    store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="worker-openai",
            base_url="https://worker-model.example.com/v1",
            api_key_secret_ref_id="secret_worker_model_key",
            default_model="gpt-enterprise",
            model_ids=["gpt-enterprise"],
            updated_by_user_id="model_admin",
        )
    )

    runner = build_agent_worker_runner(
        Settings(
            database_url=database_url,
            model_gateway_model="fallback-model",
            model_gateway_provider_store_backend="sql",
            _env_file=None,
        ),
        queue=InMemoryJobQueue(),
    )

    gateway = runner.worker.runtime.model_gateway
    assert isinstance(gateway, ModelGatewayRouter)
    assert gateway.provider_registry.providers[0].id == "worker-openai"


def test_agent_runtime_uses_policy_resolved_model_for_gateway_request():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Use the workspace policy model.",
            mode="autonomous",
        ),
    )
    gateway = RecordingModelGateway()
    runtime = AgentRuntime(
        store=store,
        model_gateway=gateway,
        model_policy=ModelPolicy(
            default_model="global-default",
            allowed_models=["global-default", "workspace-default"],
            scoped_policies=[
                ModelPolicyScope(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    default_model="workspace-default",
                    allowed_models=["workspace-default"],
                )
            ],
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert gateway.requests[0].model == "workspace-default"
