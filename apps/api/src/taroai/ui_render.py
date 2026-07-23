import re
from typing import Any

from taroai.tool_gateway import (
    ToolExecutionError,
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
)


UI_RENDER_TOOL = "ui.render"


def register_ui_render_tool_handler(gateway: ToolGateway, store: Any) -> None:
    gateway.register_tool(
        ToolPolicy(
            tool_name=UI_RENDER_TOOL,
            description=(
                "Render one compact, read-only result card inline when structured presentation "
                "materially improves a comparison, metric summary, or status result. Do not use "
                "for ordinary prose. Put the complete visible result in content using plain "
                "Markdown; tables and lists are supported. Never use HTML. Visible text must "
                "come from the conversation or observed tool evidence."
            ),
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "required": ["content", "title"],
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "title": {"type": "string", "minLength": 1, "maxLength": 160},
                    "description": {"type": "string", "maxLength": 500},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": ["block_id", "title", "spec"],
                "properties": {
                    "block_id": {"type": "string"},
                    "title": {"type": "string"},
                    "intro": {"type": "string"},
                    "spec": {"type": "object"},
                },
                "additionalProperties": False,
            },
        ),
        lambda request: _render_ui(store, request),
    )


def _render_ui(store: Any, request: ToolGatewayRequest) -> ToolResult:
    title = str(request.tool_input["title"]).strip()
    content = re.sub(
        r"<br\s*/?>", " ", str(request.tool_input["content"]).strip(), flags=re.I
    )
    if not title or not content:
        raise ToolExecutionError("UI title and content must not be empty")
    card_props = {"title": title}
    if description := str(request.tool_input.get("description") or "").strip():
        card_props["description"] = description
    elements: dict[str, dict[str, Any]] = {
        "card": {"type": "Card", "props": card_props, "children": ["content"]},
        "content": {"type": "Text", "props": {"text": content}},
    }
    spec = {"root": "card", "elements": elements}

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", request.step_id).strip("-")[:64]
    block_id = f"ui_{safe_id or request.step_id[:32]}"
    event_payload = {"blockId": block_id, "title": title, "spec": spec}
    run = store.get_run(request.tenant_id, request.run_id)
    store.append_run_event(run, "ui_render", event_payload)
    return ToolResult(
        tool_name=UI_RENDER_TOOL,
        output={"block_id": block_id, "title": title, "intro": title, "spec": spec},
    )
