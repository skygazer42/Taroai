from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import Run
from taroai.triggers.models import TriggerDefinition, TriggerType


class AgentHandoffDeniedError(PermissionError):
    pass


class AgentHandoffRequest(BaseModel):
    source_run_id: str = Field(min_length=1)
    source_agent_id: str | None = None
    reason_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_.-]+$",
    )
    handoff_depth: int = Field(default=0, ge=0)
    handoff_input: dict[str, Any] = Field(default_factory=dict)


class AgentHandoffResponse(BaseModel):
    trigger_id: str
    run_id: str
    status: str
    events_url: str


def assert_agent_handoff_allowed(
    trigger: TriggerDefinition,
    source_run: Run,
    request: AgentHandoffRequest,
) -> int:
    if trigger.type != TriggerType.AGENT_HANDOFF or trigger.agent_handoff is None:
        raise AgentHandoffDeniedError("trigger is not an agent handoff trigger")
    if trigger.workspace_id != source_run.workspace_id:
        raise AgentHandoffDeniedError("agent handoff source run is outside trigger workspace")
    if request.source_agent_id is not None and request.source_agent_id != source_run.agent_id:
        raise AgentHandoffDeniedError("agent handoff source agent does not match source run")
    target_depth = request.handoff_depth + 1
    if target_depth > trigger.agent_handoff.max_depth:
        raise AgentHandoffDeniedError("agent handoff max depth exceeded")
    return target_depth
