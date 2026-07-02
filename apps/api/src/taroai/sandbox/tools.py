from typing import Any

from taroai.sandbox.adapter import SandboxAdapter
from taroai.sandbox.browser import BrowserController
from taroai.sandbox.models import BrowserAction, BrowserActionType, SandboxCommand
from taroai.tool_gateway import ToolGateway, ToolGatewayRequest, ToolPolicy, ToolResult, ToolRiskLevel


def register_sandbox_tool_handlers(gateway: ToolGateway, adapter: SandboxAdapter) -> None:
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="sandbox.command",
            required_scopes=["sandbox.execute"],
            risk_level=ToolRiskLevel.HIGH,
        ),
        handler=lambda request: _execute_command(adapter, request),
    )


def register_browser_tool_handlers(gateway: ToolGateway, controller: BrowserController) -> None:
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="browser.action",
            required_scopes=["browser.act"],
            risk_level=ToolRiskLevel.HIGH,
        ),
        handler=lambda request: _apply_browser_action(controller, request),
    )


def _execute_command(adapter: SandboxAdapter, request: ToolGatewayRequest) -> ToolResult:
    result = adapter.execute(
        SandboxCommand(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            session_id=str(request.tool_input["session_id"]),
            command=str(request.tool_input["command"]),
            cwd=str(request.tool_input.get("cwd", "/workspace")),
            timeout_seconds=int(request.tool_input.get("timeout_seconds", 300)),
            env={
                str(key): str(value)
                for key, value in dict(request.tool_input.get("env", {})).items()
            },
        )
    )
    return ToolResult(
        tool_name=request.tool_name,
        output=result.model_dump(mode="json"),
    )


def _apply_browser_action(controller: BrowserController, request: ToolGatewayRequest) -> ToolResult:
    observation = controller.apply(
        BrowserAction(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            session_id=str(request.tool_input["session_id"]),
            action_type=BrowserActionType(str(request.tool_input["action_type"])),
            url=_optional_str(request.tool_input.get("url")),
            selector=_optional_str(request.tool_input.get("selector")),
            text=_optional_str(request.tool_input.get("text")),
            metadata=dict(request.tool_input.get("metadata", {})),
        )
    )
    return ToolResult(
        tool_name=request.tool_name,
        output=observation.model_dump(mode="json"),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
