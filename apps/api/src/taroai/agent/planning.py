from typing import Any

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    id: str
    title: str
    tool_name: str
    skill_id: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = False
