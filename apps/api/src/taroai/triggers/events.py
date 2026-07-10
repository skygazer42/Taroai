from typing import Any

from pydantic import BaseModel, Field

from taroai.triggers.models import TriggerDefinition, TriggerStatus, TriggerType


class ConnectorEvent(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    connector_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    external_event_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ConnectorEventIngestRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    connector_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    external_event_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ConnectorEventIngestRun(BaseModel):
    trigger_id: str
    run_id: str
    status: str
    events_url: str


class ConnectorEventIngestResponse(BaseModel):
    connector_id: str
    event_type: str
    external_event_id: str | None = None
    matched_trigger_count: int = Field(ge=0)
    runs: list[ConnectorEventIngestRun] = Field(default_factory=list)


def match_connector_event_triggers(
    triggers: list[TriggerDefinition],
    event: ConnectorEvent,
) -> list[TriggerDefinition]:
    return [
        trigger
        for trigger in triggers
        if connector_event_trigger_matches(trigger, event)
    ]


def connector_event_trigger_matches(
    trigger: TriggerDefinition,
    event: ConnectorEvent,
) -> bool:
    if trigger.status != TriggerStatus.ENABLED:
        return False
    if trigger.type != TriggerType.CONNECTOR_EVENT:
        return False
    if trigger.tenant_id != event.tenant_id:
        return False
    if trigger.workspace_id != event.workspace_id:
        return False
    if trigger.connector_event is None:
        return False
    if trigger.connector_event.connector_id != event.connector_id:
        return False
    if trigger.connector_event.event_type != event.event_type:
        return False
    return payload_conditions_match(
        payload=event.payload,
        payload_equals=trigger.connector_event.payload_equals,
    )


def payload_conditions_match(
    payload: dict[str, Any],
    payload_equals: dict[str, Any],
) -> bool:
    return all(
        payload_path_value(payload, path) == expected
        for path, expected in payload_equals.items()
    )


def payload_path_value(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
