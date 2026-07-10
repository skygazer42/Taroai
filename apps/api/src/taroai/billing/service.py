from pydantic import BaseModel, Field

from taroai.billing.models import (
    BillingInvoice,
    BillingInvoiceLine,
    BillingInvoiceQuery,
    BillingPricingRule,
    BillingSummaryBucket,
    BillingSummaryQuery,
)
from taroai.domain import BillingMeterEvent


class BillingPricingService(BaseModel):
    rules: list[BillingPricingRule] = Field(default_factory=list)

    def estimate_cost(
        self,
        meter_type: str,
        quantity: float,
        unit: str,
        provider: str | None = None,
        model: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        skill_id: str | None = None,
    ) -> float | None:
        rule = self.match_rule(
            meter_type=meter_type,
            unit=unit,
            provider=provider,
            model=model,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
        )
        if rule is None:
            return None
        return round(quantity * rule.price_per_unit / rule.pricing_unit_quantity, 10)

    def match_rule(
        self,
        meter_type: str,
        unit: str,
        provider: str | None = None,
        model: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        skill_id: str | None = None,
    ) -> BillingPricingRule | None:
        matching_rules = [
            rule
            for rule in self.rules
            if rule.matches(
                meter_type=meter_type,
                unit=unit,
                provider=provider,
                model=model,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
            )
        ]
        if not matching_rules:
            return None
        return sorted(
            matching_rules,
            key=lambda rule: rule.specificity(),
            reverse=True,
        )[0]


class BillingAnalyticsService(BaseModel):
    def summarize(
        self,
        meters: list[BillingMeterEvent],
        query: BillingSummaryQuery,
    ) -> list[BillingSummaryBucket]:
        buckets: dict[tuple[str | None, str, str], dict] = {}
        for meter in query.apply(meters):
            group_value = self._group_value(meter, query.group_by)
            key = (group_value, meter.meter_type, meter.unit)
            bucket = buckets.setdefault(
                key,
                {
                    "group_by": query.group_by,
                    "group_value": group_value,
                    "meter_type": meter.meter_type,
                    "unit": meter.unit,
                    "quantity": 0.0,
                    "event_count": 0,
                    "cost_estimate": None,
                },
            )
            bucket["quantity"] += meter.quantity
            bucket["event_count"] += 1
            if meter.cost_estimate is not None:
                bucket["cost_estimate"] = (bucket["cost_estimate"] or 0.0) + meter.cost_estimate
        return [
            BillingSummaryBucket(
                **{
                    **bucket,
                    "quantity": round(bucket["quantity"], 10),
                    "cost_estimate": self._rounded_cost(bucket["cost_estimate"]),
                }
            )
            for bucket in sorted(
                buckets.values(),
                key=lambda value: (
                    value["group_value"] is None,
                    value["group_value"] or "",
                    value["meter_type"],
                    value["unit"],
                ),
            )
        ]

    def _group_value(self, meter: BillingMeterEvent, group_by: str) -> str | None:
        return getattr(meter, group_by)

    def _rounded_cost(self, cost_estimate: float | None) -> float | None:
        if cost_estimate is None:
            return None
        return round(cost_estimate, 10)


class BillingInvoiceService(BaseModel):
    def create_invoice(
        self,
        tenant_id: str,
        meters: list[BillingMeterEvent],
        query: BillingInvoiceQuery,
    ) -> BillingInvoice:
        matching_meters = query.apply(
            [meter for meter in meters if meter.tenant_id == tenant_id]
        )
        lines = self._lines(matching_meters, query)
        total_cost_estimate = self._sum_costs(
            [line.cost_estimate for line in lines if line.cost_estimate is not None]
        )
        return BillingInvoice(
            tenant_id=tenant_id,
            period_start=query.period_start,
            period_end=query.period_end,
            currency=query.currency,
            group_by=query.group_by,
            meter_event_count=len(matching_meters),
            unpriced_event_count=sum(line.unpriced_event_count for line in lines),
            total_cost_estimate=total_cost_estimate,
            lines=lines,
        )

    def _lines(
        self,
        meters: list[BillingMeterEvent],
        query: BillingInvoiceQuery,
    ) -> list[BillingInvoiceLine]:
        buckets: dict[tuple[str | None, str, str, str | None, str | None], dict] = {}
        for meter in meters:
            group_value = self._group_value(meter, query.group_by)
            key = (
                group_value,
                meter.meter_type,
                meter.unit,
                meter.provider,
                meter.model,
            )
            bucket = buckets.setdefault(
                key,
                {
                    "group_by": query.group_by,
                    "group_value": group_value,
                    "meter_type": meter.meter_type,
                    "unit": meter.unit,
                    "provider": meter.provider,
                    "model": meter.model,
                    "quantity": 0.0,
                    "event_count": 0,
                    "cost_estimate": None,
                    "unpriced_event_count": 0,
                },
            )
            bucket["quantity"] += meter.quantity
            bucket["event_count"] += 1
            if meter.cost_estimate is None:
                bucket["unpriced_event_count"] += 1
            else:
                bucket["cost_estimate"] = (
                    bucket["cost_estimate"] or 0.0
                ) + meter.cost_estimate
        return [
            BillingInvoiceLine(
                **{
                    **bucket,
                    "quantity": round(bucket["quantity"], 10),
                    "cost_estimate": self._sum_costs(
                        [bucket["cost_estimate"]]
                        if bucket["cost_estimate"] is not None
                        else []
                    ),
                }
            )
            for bucket in sorted(
                buckets.values(),
                key=lambda value: (
                    value["group_value"] is None,
                    value["group_value"] or "",
                    value["meter_type"],
                    value["unit"],
                    value["provider"] or "",
                    value["model"] or "",
                ),
            )
        ]

    def _group_value(self, meter: BillingMeterEvent, group_by: str) -> str | None:
        return getattr(meter, group_by)

    def _sum_costs(self, costs: list[float]) -> float | None:
        if not costs:
            return None
        return round(sum(costs), 10)
