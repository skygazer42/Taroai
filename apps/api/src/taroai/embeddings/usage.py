from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.billing import BillingPricingService
from taroai.embeddings.models import EmbeddingGatewayResponse


EMBEDDING_AUDIT_EVENT_TYPE = "embedding.gateway.called"
EMBEDDING_CALL_METER_TYPE = "embedding_call_count"
EMBEDDING_TOKEN_METER_TYPE = "embedding_tokens"
EMBEDDING_PROVIDER = "openai_compatible"

SAFE_EMBEDDING_METADATA_KEYS = {
    "allowed_workspace_count",
    "chunk_count",
    "clearance_level",
    "knowledge_base_id",
    "source_document_id",
}


class EmbeddingUsageRecord(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = None
    user_id: str = Field(min_length=1)
    run_id: str | None = None
    purpose: Literal["knowledge_index", "knowledge_query", "memory_query"]
    response: EmbeddingGatewayResponse
    input_count: int = Field(ge=1)
    provider: str = Field(default=EMBEDDING_PROVIDER, min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def safe_metadata(self) -> dict[str, Any]:
        usage = (
            self.response.usage.model_dump(mode="json")
            if self.response.usage is not None
            else None
        )
        metadata = {
            "purpose": self.purpose,
            "provider": self.provider,
            "model": self.response.model,
            "input_count": self.input_count,
            "embedding_count": len(self.response.embeddings),
            "usage": usage,
        }
        metadata.update(
            {
                key: value
                for key, value in self.metadata.items()
                if key in SAFE_EMBEDDING_METADATA_KEYS
            }
        )
        return metadata


class EmbeddingUsageRecorder(BaseModel):
    store: Any = Field(exclude=True, repr=False)
    audit_service: Any | None = Field(default=None, exclude=True, repr=False)
    billing_pricing_service: BillingPricingService = Field(default_factory=BillingPricingService)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def record(
        self,
        record: EmbeddingUsageRecord,
        actor: AuditActor | None = None,
    ) -> None:
        metadata = record.safe_metadata()
        audit_service = self.audit_service or AuditService(store=self.store)
        audit_service.record(
            AuditEventCreate(
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                user_id=record.user_id,
                run_id=record.run_id,
                event_type=EMBEDDING_AUDIT_EVENT_TYPE,
                metadata=metadata,
                actor=actor or self._default_actor(record),
            )
        )
        if record.run_id is None:
            if record.workspace_id is None:
                return
        self.store.record_billing_meter(
            tenant_id=record.tenant_id,
            run_id=record.run_id,
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            meter_type=EMBEDDING_CALL_METER_TYPE,
            quantity=1,
            unit="call",
            provider=record.provider,
            model=record.response.model,
            cost_estimate=self._estimate_cost(
                meter_type=EMBEDDING_CALL_METER_TYPE,
                quantity=1,
                unit="call",
                provider=record.provider,
                model=record.response.model,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
            ),
            metadata=metadata,
        )
        if record.response.usage is None or record.response.usage.total_tokens <= 0:
            return
        self.store.record_billing_meter(
            tenant_id=record.tenant_id,
            run_id=record.run_id,
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            meter_type=EMBEDDING_TOKEN_METER_TYPE,
            quantity=record.response.usage.total_tokens,
            unit="token",
            provider=record.provider,
            model=record.response.model,
            cost_estimate=self._estimate_cost(
                meter_type=EMBEDDING_TOKEN_METER_TYPE,
                quantity=record.response.usage.total_tokens,
                unit="token",
                provider=record.provider,
                model=record.response.model,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
            ),
            metadata=metadata,
        )

    def _estimate_cost(
        self,
        meter_type: str,
        quantity: float,
        unit: str,
        provider: str | None,
        model: str | None,
        tenant_id: str | None,
        workspace_id: str | None,
    ) -> float | None:
        return self.billing_pricing_service.estimate_cost(
            meter_type=meter_type,
            quantity=quantity,
            unit=unit,
            provider=provider,
            model=model,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    def _default_actor(self, record: EmbeddingUsageRecord) -> AuditActor:
        return AuditActor(
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            actor_type="user",
        )
