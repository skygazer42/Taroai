from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from taroai.domain import RunMode, new_id, utc_now


class TriggerType(str, Enum):
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    API = "api"
    CONNECTOR_EVENT = "connector_event"
    AGENT_HANDOFF = "agent_handoff"


class TriggerStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class TriggerScheduleConfig(BaseModel):
    cron_expression: str = Field(min_length=1)
    timezone: str = Field(default="UTC", min_length=1)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_catch_up_runs: int = Field(default=1, ge=1)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def validate_time_window(self) -> "TriggerScheduleConfig":
        if self.starts_at is not None and self.ends_at is not None:
            if self.starts_at > self.ends_at:
                raise ValueError("schedule starts_at must be before ends_at")
        return self


class TriggerConnectorEventConfig(BaseModel):
    connector_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    payload_equals: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload_equals")
    @classmethod
    def validate_payload_equals(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, expected in value.items():
            if not key.strip():
                raise ValueError("connector event payload match keys must not be empty")
            if not isinstance(expected, (str, int, float, bool)) and expected is not None:
                raise ValueError("connector event payload match values must be scalar")
        return value


class TriggerAgentHandoffConfig(BaseModel):
    target_agent_id: str = Field(min_length=1)
    max_depth: int = Field(default=1, ge=1)
    required_permissions: list[str] = Field(default_factory=list)

    @field_validator("required_permissions")
    @classmethod
    def validate_required_permissions(cls, value: list[str]) -> list[str]:
        normalized = [permission.strip() for permission in value]
        if any(not permission for permission in normalized):
            raise ValueError("agent handoff required permissions must not be empty")
        return list(dict.fromkeys(normalized))


class TriggerDefinitionCreate(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    agent_id: str | None = None
    created_by_user_id: str | None = None
    service_account_id: str | None = None
    type: TriggerType
    name: str = Field(min_length=1)
    status: TriggerStatus = TriggerStatus.ENABLED
    input_template: dict[str, Any] = Field(default_factory=dict)
    policy_profile: str | None = None
    budget_profile: str | None = None
    schedule: TriggerScheduleConfig | None = None
    connector_event: TriggerConnectorEventConfig | None = None
    agent_handoff: TriggerAgentHandoffConfig | None = None
    next_run_at: datetime | None = None

    @model_validator(mode="after")
    def validate_trigger_contract(self) -> "TriggerDefinitionCreate":
        if not self.created_by_user_id and not self.service_account_id:
            raise ValueError("trigger requires created_by_user_id or service_account_id")
        if self.type == TriggerType.SCHEDULE and self.schedule is None:
            raise ValueError("schedule trigger requires schedule config")
        if self.type != TriggerType.SCHEDULE and self.schedule is not None:
            raise ValueError("schedule config is only valid for schedule triggers")
        if self.type == TriggerType.CONNECTOR_EVENT and self.connector_event is None:
            raise ValueError("connector event trigger requires connector_event config")
        if self.type != TriggerType.CONNECTOR_EVENT and self.connector_event is not None:
            raise ValueError("connector_event config is only valid for connector event triggers")
        if self.type == TriggerType.AGENT_HANDOFF and self.agent_handoff is None:
            raise ValueError("agent handoff trigger requires agent_handoff config")
        if self.type != TriggerType.AGENT_HANDOFF and self.agent_handoff is not None:
            raise ValueError("agent_handoff config is only valid for agent handoff triggers")
        return self


class TriggerCreateRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    agent_id: str | None = None
    service_account_id: str | None = None
    type: TriggerType
    name: str = Field(min_length=1)
    status: TriggerStatus = TriggerStatus.ENABLED
    input_template: dict[str, Any] = Field(default_factory=dict)
    policy_profile: str | None = None
    budget_profile: str | None = None
    schedule: TriggerScheduleConfig | None = None
    connector_event: TriggerConnectorEventConfig | None = None
    agent_handoff: TriggerAgentHandoffConfig | None = None
    next_run_at: datetime | None = None

    @model_validator(mode="after")
    def validate_schedule_contract(self) -> "TriggerCreateRequest":
        if self.type == TriggerType.SCHEDULE and self.schedule is None:
            raise ValueError("schedule trigger requires schedule config")
        if self.type != TriggerType.SCHEDULE and self.schedule is not None:
            raise ValueError("schedule config is only valid for schedule triggers")
        if self.type == TriggerType.CONNECTOR_EVENT and self.connector_event is None:
            raise ValueError("connector event trigger requires connector_event config")
        if self.type != TriggerType.CONNECTOR_EVENT and self.connector_event is not None:
            raise ValueError("connector_event config is only valid for connector event triggers")
        if self.type == TriggerType.AGENT_HANDOFF and self.agent_handoff is None:
            raise ValueError("agent handoff trigger requires agent_handoff config")
        if self.type != TriggerType.AGENT_HANDOFF and self.agent_handoff is not None:
            raise ValueError("agent_handoff config is only valid for agent handoff triggers")
        return self


class TriggerDefinition(TriggerDefinitionCreate):
    id: str = Field(default_factory=lambda: new_id("trigger"))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TriggerInvokeRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class TriggerRunRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: TriggerType
    requested_by_user_id: str = Field(min_length=1)
    agent_id: str | None = None
    message: str = Field(min_length=1)
    mode: RunMode = RunMode.AUTONOMOUS
    invocation_payload_keys: list[str] = Field(default_factory=list)


class TriggerInvokeResponse(BaseModel):
    trigger_id: str
    run_id: str
    status: str
    events_url: str
