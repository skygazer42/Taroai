from pydantic import BaseModel, Field

from taroai.agent.planning import PlanStep
from taroai.agent.tools import ToolResult
from taroai.domain import RunStatus
from taroai.knowledge import RetrievalResult
from taroai.memory import MemoryRecord


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
