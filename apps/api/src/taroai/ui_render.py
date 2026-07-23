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
                "Render one compact result card inline when structured presentation materially "
                "improves a comparison, metric summary, status result, or short follow-up form. Do not use "
                "for ordinary prose. Put the complete visible result in content using plain "
                "Markdown; tables and lists are supported. Never use HTML. Visible text must "
                "come from the conversation or observed tool evidence. Optional actions only send "
                "their declared message back into this chat; charts are bar charts."
            ),
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "required": ["content", "title"],
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "title": {"type": "string", "minLength": 1, "maxLength": 160},
                    "description": {"type": "string", "maxLength": 500},
                    "form": {
                        "type": "object",
                        "required": ["fields"],
                        "properties": {
                            "fields": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 8,
                                "items": {
                                    "type": "object",
                                    "required": ["name", "label", "type"],
                                    "properties": {
                                        "name": {"type": "string", "minLength": 1, "maxLength": 64},
                                        "label": {"type": "string", "minLength": 1, "maxLength": 120},
                                        "type": {"type": "string", "enum": ["text", "email", "number", "date", "select"]},
                                        "placeholder": {"type": "string", "maxLength": 160},
                                        "required": {"type": "boolean"},
                                        "options": {
                                            "type": "array",
                                            "maxItems": 12,
                                            "items": {"type": "string", "minLength": 1, "maxLength": 120},
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                            },
                            "submit_label": {"type": "string", "maxLength": 80},
                        },
                        "additionalProperties": False,
                    },
                    "actions": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "required": ["label", "message"],
                            "properties": {
                                "label": {"type": "string", "minLength": 1, "maxLength": 80},
                                "message": {"type": "string", "minLength": 1, "maxLength": 500},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "chart": {
                        "type": "object",
                        "required": ["labels", "values"],
                        "properties": {
                            "labels": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "string", "maxLength": 80}},
                            "values": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "number", "minimum": 0}},
                        },
                        "additionalProperties": False,
                    },
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
    children = ["content"]
    elements: dict[str, dict[str, Any]] = {
        "card": {"type": "Card", "props": card_props, "children": children},
        "content": {"type": "Text", "props": {"text": content}},
    }
    if chart := request.tool_input.get("chart"):
        elements["chart"] = {"type": "BarChart", "props": chart}
        children.append("chart")
    if form := request.tool_input.get("form"):
        field_ids = []
        for index, field in enumerate(form["fields"]):
            field_id = f"field-{index}"
            field_ids.append(field_id)
            elements[field_id] = {"type": "Input", "props": field}
        elements["submit"] = {
            "type": "Button",
            "props": {"label": form.get("submit_label") or "提交", "submit": True},
        }
        elements["form"] = {
            "type": "Form",
            "children": [*field_ids, "submit"],
        }
        children.append("form")
    if actions := request.tool_input.get("actions"):
        action_ids = []
        for index, action in enumerate(actions):
            action_id = f"action-{index}"
            action_ids.append(action_id)
            elements[action_id] = {"type": "Button", "props": action}
        elements["actions"] = {
            "type": "Stack",
            "props": {"direction": "horizontal", "gap": "sm"},
            "children": action_ids,
        }
        children.append("actions")
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
