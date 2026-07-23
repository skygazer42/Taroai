from typing import Any

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    id: str
    title: str
    tool_name: str
    skill_id: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = False
    depends_on: list[str] | None = None
    phase_id: str | None = None
    phase_title: str | None = None
    tool_mode: str = "standard"
    model_hint: str = "strong"
