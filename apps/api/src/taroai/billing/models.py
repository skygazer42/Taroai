from typing import Literal

from pydantic import BaseModel

from taroai.domain import BillingMeterEvent


BillingSummaryGroupBy = Literal[
    "workspace_id",
    "user_id",
    "agent_id",
    "skill_id",
    "meter_type",
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
