import json
from typing import Any

from taroai.tool_gateway import (
    ToolExecutionError,
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
)


OBSERVATION_READ_TOOL = "observation.read"

# 每页字符数低于 _model_observations 的 12K 压缩阈值，读回的分页不会再被压缩。
OBSERVATION_READ_PAGE_CHARACTERS = 10_000


def register_observation_read_tool_handler(gateway: ToolGateway, store: Any) -> None:
    gateway.register_tool(
        ToolPolicy(
            tool_name=OBSERVATION_READ_TOOL,
            description=(
                "Re-read the full stored output of an earlier observation in this run "
                "when its inline version was compacted or dropped from the recent "
                "window. Pass the observation's action_id, and an optional character "
                "offset to page through long outputs."
            ),
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "required": ["action_id"],
                "properties": {
                    "action_id": {"type": "string", "minLength": 1},
                    "offset": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": ["action_id", "content", "total_characters", "offset"],
                "properties": {
                    "action_id": {"type": "string"},
                    "content": {"type": "string"},
                    "total_characters": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "next_offset": {"type": ["integer", "null"]},
                },
                "additionalProperties": False,
            },
        ),
        lambda request: _read_observation(store, request),
    )


def _read_observation(store: Any, request: ToolGatewayRequest) -> ToolResult:
    action_id = str(request.tool_input["action_id"]).strip()
    offset = max(0, int(request.tool_input.get("offset") or 0))
    action = store.get_agent_action(request.tenant_id, action_id)
    if action.run_id != request.run_id:
        raise ToolExecutionError("observation belongs to a different run")
    if action.observation is None:
        raise ToolExecutionError("observation has no recorded output yet")
    content = json.dumps(
        action.observation.output, ensure_ascii=False, default=str
    )
    total_characters = len(content)
    page_end = offset + OBSERVATION_READ_PAGE_CHARACTERS
    return ToolResult(
        tool_name=OBSERVATION_READ_TOOL,
        output={
            "action_id": action_id,
            "content": content[offset:page_end],
            "total_characters": total_characters,
            "offset": offset,
            "next_offset": page_end if page_end < total_characters else None,
        },
    )
