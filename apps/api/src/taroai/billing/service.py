from pydantic import BaseModel

from taroai.billing.models import BillingSummaryBucket, BillingSummaryQuery
from taroai.domain import BillingMeterEvent


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
