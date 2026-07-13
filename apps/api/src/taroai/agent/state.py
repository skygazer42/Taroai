from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from taroai.agent.models import (
    AgentDecision,
    AgentObservation,
    AgentVerificationResult,
)
from taroai.agent.planning import PlanStep
from taroai.agent.tools import ToolResult
from taroai.domain import RunStatus, utc_now
from taroai.knowledge import RetrievalResult
from taroai.memory import MemoryRecord


AgentGraphRoute = Literal[
    "observe",
    "decide",
    "policy",
    "act",
    "observe_result",
    "verify",
    "repair",
    "replan",
    "complete",
    "wait_user",
    "fail",
    "end",
]


class AgentRetrievedContext(BaseModel):
    knowledge_results: list[RetrievalResult] = Field(default_factory=list)
    memory_records: list[MemoryRecord] = Field(default_factory=list)


class AgentRuntimeState(BaseModel):
    tenant_id: str
    workspace_id: str
    user_id: str
    run_id: str
    goal: str
    status: RunStatus
    plan: list[PlanStep] = Field(default_factory=list)
    current_step_id: str | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    approved_step_ids: list[str] = Field(default_factory=list)
    approved_guardrail_keys: list[str] = Field(default_factory=list)
    pending_guardrail_approval_key: str | None = None
    pending_guardrail_approval_stage: str | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    retrieved_context: AgentRetrievedContext = Field(
        default_factory=AgentRetrievedContext
    )
    sandbox_session_id: str | None = None
    browser_session_id: str | None = None
    promoted_sandbox_artifact_paths: list[str] = Field(default_factory=list)
    approval_id: str | None = None
    failure_reason: str | None = None
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=20, ge=1)
    observations: list[AgentObservation] = Field(default_factory=list)
    active_plan_revision: int = Field(default=1, ge=1)
    pending_actions: list[AgentDecision] = Field(default_factory=list)
    verifier_result: AgentVerificationResult | None = None
    repair_attempts: int = Field(default=0, ge=0)
    replan_count: int = Field(default=0, ge=0)
    steering_messages: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    deadline_at: datetime | None = None
    checkpoint_sequence: int = Field(default=0, ge=0)
    max_repairs: int = Field(default=4, ge=0)
    cost_limit: float = Field(default=0, ge=0)
    cost_consumed: float = Field(default=0, ge=0)
    last_decision: AgentDecision | None = None
    final_response_text: str | None = None
    pending_uncertain_action_id: str | None = None
    waiting_reason: str | None = None
    terminal_event_emitted: bool = False
    # LangGraph 节点之间的显式控制状态。
    graph_route: AgentGraphRoute = "observe"
    current_cycle_id: str | None = None
    current_action_id: str | None = None
    graph_failure_code: str | None = None
    graph_failure_detail: str | None = None
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)
