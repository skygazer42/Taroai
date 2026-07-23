import base64
import json
from typing import Any

from taroai.sandbox.adapter import SandboxAdapter
from taroai.sandbox.browser import BrowserController
from taroai.sandbox.models import BrowserAction, BrowserActionType, SandboxCommand
from taroai.store import NotFoundError
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

SANDBOX_COMMAND_INPUT_SCHEMA = {
    "type": "object",
    "required": ["command"],
    "properties": {
        "command": {
            "type": "string",
            "minLength": 1,
            "description": (
                "POSIX shell command to run inside /workspace. For Python, use "
                "python3 -c or a shell heredoc; never pass bare Python source."
            ),
        },
        "cwd": {"type": "string", "default": "/workspace"},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
        "result_mode": {
            "type": "string",
            "enum": ["raw_stdout", "summarize"],
            "description": (
                "Use raw_stdout only when short stdout is the complete user-facing "
                "answer and needs no explanation; otherwise use summarize or omit it."
            ),
        },
        "env": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "artifact_path": {
            "type": "string",
            "description": "Exact /workspace/artifacts file created by the command; omit for stdout-only work.",
        },
        "artifact_paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Exact /workspace/artifacts files created by the command; omit for stdout-only work.",
        },
        # 由运行时注入，模型无需生成。
        "session_id": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}

BROWSER_ACTION_INPUT_SCHEMA = {
    "type": "object",
    "required": ["action_type"],
    "properties": {
        "action_type": {
            "type": "string",
            "enum": ["navigate", "click", "type", "screenshot", "extract"],
        },
        # session_id 由运行时注入，模型不需要生成。
        "session_id": {"type": "string", "minLength": 1},
        "url": {"type": ["string", "null"]},
        "selector": {"type": ["string", "null"]},
        "text": {"type": ["string", "null"]},
        "metadata": {"type": "object"},
    },
    "additionalProperties": False,
}


def register_sandbox_tool_handlers(gateway: ToolGateway, adapter: SandboxAdapter) -> None:
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="sandbox.command",
            description=(
                "Run one POSIX shell command in the isolated thread workspace. "
                "Do not use it merely to check a standard formula or straightforward arithmetic "
                "unless the user explicitly requests code or sandbox execution. "
                "Wrap Python with python3 -c or a shell heredoc; never pass bare Python "
                "source. Declare only artifact files that the command actually creates. "
                "Set result_mode=raw_stdout only when stdout itself is the complete answer."
            ),
            required_scopes=["sandbox.execute"],
            risk_level=ToolRiskLevel.HIGH,
            input_schema=SANDBOX_COMMAND_INPUT_SCHEMA,
        ),
        handler=lambda request: _execute_command(adapter, request),
    )


def register_browser_tool_handlers(
    gateway: ToolGateway,
    controller: BrowserController,
    profile_service: Any | None = None,
) -> None:
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="browser.action",
            description=(
                "Interact with a browser page by navigating, clicking, typing, "
                "capturing a screenshot, or extracting visible content."
            ),
            required_scopes=["browser.act"],
            risk_level=ToolRiskLevel.HIGH,
            input_schema=BROWSER_ACTION_INPUT_SCHEMA,
        ),
        handler=lambda request: _apply_browser_action(
            controller, request, profile_service=profile_service
        ),
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
            id=request.step_id,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            thread_id=request.thread_id,
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


def _apply_browser_action(
    controller: BrowserController,
    request: ToolGatewayRequest,
    profile_service: Any | None = None,
) -> ToolResult:
    action = BrowserAction(
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
    if profile_service is not None:
        try:
            observation = profile_service.apply_action(
                tenant_id=request.tenant_id,
                session_id=action.session_id,
                action_type=action.action_type,
                url=action.url,
                selector=action.selector,
                text=action.text,
                metadata=action.metadata,
            )
        except NotFoundError:
            observation = controller.apply(action)
    else:
        observation = controller.apply(action)
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
