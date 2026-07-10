from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from taroai.domain import BillingMeterEvent


BillingSummaryGroupBy = Literal[
    "workspace_id",
    "user_id",
    "agent_id",
    "skill_id",
    "meter_type",
]
BillingInvoiceGroupBy = Literal[
    "meter_type",
    "workspace_id",
    "user_id",
    "agent_id",
    "skill_id",
]


class BillingMeterQuery(BaseModel):
    run_id: str | None = None
    workspace_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    skill_id: str | None = None
    meter_type: str | None = None

    def apply(self, meters: list[BillingMeterEvent]) -> list[BillingMeterEvent]:
        return [meter for meter in meters if self.matches(meter)]

    def matches(self, meter: BillingMeterEvent) -> bool:
        return all(
            [
                self.run_id is None or meter.run_id == self.run_id,
                self.workspace_id is None or meter.workspace_id == self.workspace_id,
                self.user_id is None or meter.user_id == self.user_id,
                self.agent_id is None or meter.agent_id == self.agent_id,
                self.skill_id is None or meter.skill_id == self.skill_id,
                self.meter_type is None or meter.meter_type == self.meter_type,
            ]
        )


class BillingSummaryQuery(BillingMeterQuery):
    group_by: BillingSummaryGroupBy = "workspace_id"


class BillingSummaryBucket(BaseModel):
    group_by: BillingSummaryGroupBy
    group_value: str | None
    meter_type: str
    unit: str
    quantity: float
    event_count: int
    cost_estimate: float | None = None


class BillingInvoiceQuery(BillingMeterQuery):
    period_start: datetime | None = None
    period_end: datetime | None = None
    group_by: BillingInvoiceGroupBy = "meter_type"
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_period(self):
        period_start = self._normalize_datetime(self.period_start)
        period_end = self._normalize_datetime(self.period_end)
        if period_start is not None and period_end is not None and period_end < period_start:
            raise ValueError("period_end must be greater than or equal to period_start")
        self.period_start = period_start
        self.period_end = period_end
        return self

    def matches(self, meter: BillingMeterEvent) -> bool:
        period_start = self._normalize_datetime(self.period_start)
        period_end = self._normalize_datetime(self.period_end)
        created_at = self._normalize_datetime(meter.created_at)
        return super().matches(meter) and all(
            [
                period_start is None or created_at >= period_start,
                period_end is None or created_at <= period_end,
            ]
        )

    def _normalize_datetime(self, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)


class BillingInvoiceLine(BaseModel):
    group_by: BillingInvoiceGroupBy
    group_value: str | None
    meter_type: str
    unit: str
    provider: str | None = None
    model: str | None = None
    quantity: float
    event_count: int
    cost_estimate: float | None = None
    unpriced_event_count: int = 0


class BillingInvoice(BaseModel):
    tenant_id: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    group_by: BillingInvoiceGroupBy
    meter_event_count: int
    unpriced_event_count: int
    total_cost_estimate: float | None = None
    lines: list[BillingInvoiceLine] = Field(default_factory=list)


class BillingInvoiceRecord(BaseModel):
    invoice_id: str
    tenant_id: str
    invoice: BillingInvoice
    created_by_user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_tenant(self):
        if self.invoice.tenant_id != self.tenant_id:
            raise ValueError("invoice tenant_id must match record tenant_id")
        return self


class BillingPricingRule(BaseModel):
    tenant_id: str | None = None
    workspace_id: str | None = None
    skill_id: str | None = None
    meter_type: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    price_per_unit: float = Field(ge=0)
    pricing_unit_quantity: float = Field(default=1, gt=0)
    provider: str | None = None
    model: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @model_validator(mode="after")
    def require_tenant_for_workspace(self):
        if self.workspace_id is not None and self.tenant_id is None:
            raise ValueError("tenant_id is required when workspace_id is set")
        return self

    def matches(
        self,
        meter_type: str,
        unit: str,
        provider: str | None,
        model: str | None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        skill_id: str | None = None,
    ) -> bool:
        return all(
            [
                self.tenant_id is None or self.tenant_id == tenant_id,
                self.workspace_id is None or self.workspace_id == workspace_id,
                self.skill_id is None or self.skill_id == skill_id,
                self.meter_type == meter_type,
                self.unit == unit,
                self.provider is None or self.provider == provider,
                self.model is None or self.model == model,
            ]
        )

    def specificity(self) -> int:
        score = 0
        if self.tenant_id is not None:
            score += 4
        if self.workspace_id is not None:
            score += 8
        if self.skill_id is not None:
            score += 16
        if self.provider is not None:
            score += 2
        if self.model is not None:
            score += 1
        return score


class BillingPricingRuleApiUpsert(BaseModel):
    workspace_id: str | None = None
    skill_id: str | None = None
    meter_type: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    price_per_unit: float = Field(ge=0)
    pricing_unit_quantity: float = Field(default=1, gt=0)
    provider: str | None = None
    model: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)

    def to_upsert(
        self,
        tenant_id: str,
        updated_by_user_id: str,
    ) -> "BillingPricingRuleUpsert":
        return BillingPricingRuleUpsert(
            tenant_id=tenant_id,
            workspace_id=self.workspace_id,
            skill_id=self.skill_id,
            meter_type=self.meter_type,
            unit=self.unit,
            price_per_unit=self.price_per_unit,
            pricing_unit_quantity=self.pricing_unit_quantity,
            provider=self.provider,
            model=self.model,
            currency=self.currency,
            updated_by_user_id=updated_by_user_id,
        )


class BillingPricingRuleUpsert(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = None
    skill_id: str | None = None
    meter_type: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    price_per_unit: float = Field(ge=0)
    pricing_unit_quantity: float = Field(default=1, gt=0)
    provider: str | None = None
    model: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    updated_by_user_id: str | None = None

    def to_pricing_rule(self) -> BillingPricingRule:
        return BillingPricingRule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            skill_id=self.skill_id,
            meter_type=self.meter_type,
            unit=self.unit,
            price_per_unit=self.price_per_unit,
            pricing_unit_quantity=self.pricing_unit_quantity,
            provider=self.provider,
            model=self.model,
            currency=self.currency,
        )


class BillingPricingRuleRecord(BaseModel):
    tenant_id: str
    workspace_id: str | None = None
    skill_id: str | None = None
    meter_type: str
    unit: str
    price_per_unit: float
    pricing_unit_quantity: float = 1
    provider: str | None = None
    model: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    updated_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_pricing_rule(self) -> BillingPricingRule:
        return BillingPricingRule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            skill_id=self.skill_id,
            meter_type=self.meter_type,
            unit=self.unit,
            price_per_unit=self.price_per_unit,
            pricing_unit_quantity=self.pricing_unit_quantity,
            provider=self.provider,
            model=self.model,
            currency=self.currency,
        )
