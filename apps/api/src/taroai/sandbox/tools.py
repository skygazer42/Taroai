import base64
import json
from typing import Any

from taroai.sandbox.adapter import SandboxAdapter
from taroai.sandbox.browser import BrowserController
from taroai.sandbox.models import BrowserAction, BrowserActionType, SandboxCommand
from taroai.secrets import SecretLease
from taroai.tool_gateway import (
    ToolExecutionError,
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
)


SANDBOX_SECRET_LEASES_ENV = "TAROAI_SECRET_LEASES"
SANDBOX_SECRET_LEASE_COUNT_ENV = "TAROAI_SECRET_LEASE_COUNT"
SANDBOX_SECRET_LEASE_ENV_PREFIX = "TAROAI_SECRET_LEASE"


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
    command_env = _sandbox_command_env(
        custom_env={
            str(key): str(value)
            for key, value in dict(request.tool_input.get("env", {})).items()
        },
        secret_leases=request.secret_leases,
    )
    result = adapter.execute(
        SandboxCommand(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            session_id=str(request.tool_input["session_id"]),
            command=str(request.tool_input["command"]),
            cwd=str(request.tool_input.get("cwd", "/workspace")),
            timeout_seconds=int(request.tool_input.get("timeout_seconds", 300)),
            env=command_env,
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
    output = observation.model_dump(mode="json")
    if observation.screenshot_content is not None:
        output["screenshot_content_base64"] = base64.b64encode(
            observation.screenshot_content
        ).decode("ascii")
    return ToolResult(
        tool_name=request.tool_name,
        output=output,
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _sandbox_command_env(
    custom_env: dict[str, str],
    secret_leases: list[SecretLease],
) -> dict[str, str]:
    reserved_keys = sorted(
        key
        for key in custom_env
        if key == SANDBOX_SECRET_LEASES_ENV
        or key == SANDBOX_SECRET_LEASE_COUNT_ENV
        or key.startswith(f"{SANDBOX_SECRET_LEASE_ENV_PREFIX}_")
    )
    if reserved_keys:
        raise ToolExecutionError(
            f"sandbox command env contains reserved secret lease env keys: {', '.join(reserved_keys)}"
        )
    if not secret_leases:
        return custom_env
    lease_payload = [
        lease.model_dump(mode="json")
        for lease in secret_leases
    ]
    return custom_env | {
        SANDBOX_SECRET_LEASES_ENV: json.dumps(
            lease_payload,
            sort_keys=True,
            separators=(",", ":"),
        ),
        SANDBOX_SECRET_LEASE_COUNT_ENV: str(len(secret_leases)),
    }
