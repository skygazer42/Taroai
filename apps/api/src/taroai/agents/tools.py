from typing import Any

from taroai.agents.models import AgentDefinitionCreate, AgentVersionSpec
from taroai.agents.service import AgentRegistryService
from taroai.tool_gateway import (
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
)


CREATE_AGENT_DRAFT_TOOL = "agent.create_draft"
UPDATE_AGENT_DRAFT_TOOL = "agent.update_draft"


def register_agent_tool_handlers(
    gateway: ToolGateway,
    service: AgentRegistryService,
) -> None:
    """Expose the two reversible Agent-App writes the chat runtime needs."""

    gateway.register_tool(
        ToolPolicy(
            tool_name=CREATE_AGENT_DRAFT_TOOL,
            description=(
                "Create a draft reusable Agent or Workflow App only when the user "
                "explicitly asks to create one. The draft remains reviewable and unpublished. "
                "The required instructions field must contain the complete reusable behavior."
            ),
            risk_level=ToolRiskLevel.MEDIUM,
            input_schema={
                "type": "object",
                "required": ["name", "instructions"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 160},
                    "description": {"type": "string", "maxLength": 2000},
                    "app_kind": {"type": "string", "enum": ["agent", "workflow"]},
                    "write_autonomy": {
                        "type": "string",
                        "enum": ["approval_required", "full_auto"],
                    },
                    "instructions": {"type": "string", "minLength": 1},
                    "input_schema": {"type": "object"},
                    "output_contract": {"type": "object"},
                    "skill_bindings": {"type": "array", "items": {"type": "object"}},
                    "connector_bindings": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "additionalProperties": False,
            },
            output_schema=_agent_tool_output_schema(),
        ),
        lambda request: _create_agent_draft(service, request),
    )
    gateway.register_tool(
        ToolPolicy(
            tool_name=UPDATE_AGENT_DRAFT_TOOL,
            description=(
                "Update an existing Agent or Workflow App only when the user explicitly "
                "asks. Instruction changes create a new immutable draft version."
            ),
            risk_level=ToolRiskLevel.MEDIUM,
            input_schema={
                "type": "object",
                "required": ["agent_id"],
                "properties": {
                    "agent_id": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1, "maxLength": 160},
                    "description": {"type": "string", "maxLength": 2000},
                    "app_kind": {"type": "string", "enum": ["agent", "workflow"]},
                    "write_autonomy": {
                        "type": "string",
                        "enum": ["approval_required", "full_auto"],
                    },
                    "instructions": {"type": "string", "minLength": 1},
                    "change_note": {"type": "string", "maxLength": 2000},
                },
                "additionalProperties": False,
            },
            output_schema=_agent_tool_output_schema(),
        ),
        lambda request: _update_agent_draft(service, request),
    )


def _create_agent_draft(
    service: AgentRegistryService,
    request: ToolGatewayRequest,
) -> ToolResult:
    tool_input = request.tool_input
    run = service.store.get_run(request.tenant_id, request.run_id)
    bindings = {
        kind: {
            str(item.get("id") or item.get(f"{kind}_id")): dict(item)
            for item in tool_input.get(f"{kind}_bindings") or []
            if item.get("id") or item.get(f"{kind}_id")
        }
        for kind in ("skill", "connector")
    }
    for reference in run.resource_refs:
        if reference.type not in bindings:
            continue
        bindings[reference.type][reference.id] = {
            **bindings[reference.type].get(reference.id, {}),
            "id": reference.id,
            **({"version": reference.version} if reference.version else {}),
        }
    for item in service._runtime_snapshot(request.tenant_id, request.run_id).get(
        "runtime_metadata", {}
    ).get("used_skills", []):
        skill_id = str(item.get("skill_id") or item.get("id") or "")
        if skill_id:
            bindings["skill"][skill_id] = {
                "id": skill_id,
                **{
                    key: item[key]
                    for key in ("version", "package_digest", "source_digest")
                    if item.get(key)
                },
            }
    definition, version = service.create(
        request.tenant_id,
        request.user_id,
        AgentDefinitionCreate(
            workspace_id=request.workspace_id,
            name=str(tool_input["name"]).strip(),
            description=str(tool_input.get("description") or "").strip(),
            app_kind=tool_input.get("app_kind", "agent"),
            write_autonomy=tool_input.get("write_autonomy", "approval_required"),
            version=AgentVersionSpec(
                instructions=str(tool_input["instructions"]).strip(),
                input_schema=tool_input.get("input_schema")
                or {"type": "object", "properties": {}},
                output_contract=tool_input.get("output_contract") or {},
                skill_bindings=list(bindings["skill"].values()),
                connector_bindings=list(bindings["connector"].values()),
                source_thread_id=request.thread_id,
                source_run_id=request.run_id,
                change_note="Created from chat",
            ),
        ),
        source_run_id=request.run_id,
    )
    _emit_agent_event(service, request, "app_created", definition, version.version)
    return _agent_result(CREATE_AGENT_DRAFT_TOOL, definition, version.version)


def _update_agent_draft(
    service: AgentRegistryService,
    request: ToolGatewayRequest,
) -> ToolResult:
    tool_input = request.tool_input
    agent_id = str(tool_input["agent_id"])
    definition = service.registry.get(request.tenant_id, agent_id)
    if definition.workspace_id != request.workspace_id:
        raise ValueError("Agent is not available in this workspace")

    metadata = {
        key: tool_input[key]
        for key in ("name", "description", "app_kind", "write_autonomy")
        if key in tool_input
    }
    if metadata:
        definition = service.update_definition(
            request.tenant_id,
            request.user_id,
            agent_id,
            source_run_id=request.run_id,
            **metadata,
        )

    version_number = definition.latest_version
    if "instructions" in tool_input:
        current = service.registry.get_version(
            request.tenant_id, agent_id, definition.latest_version
        )
        spec = current.spec.model_copy(
            update={
                "instructions": str(tool_input["instructions"]).strip(),
                "source_thread_id": request.thread_id,
                "source_run_id": request.run_id,
                "change_note": str(
                    tool_input.get("change_note") or "Updated from chat"
                ).strip(),
            },
            deep=True,
        )
        version = service.create_version(
            request.tenant_id, request.user_id, agent_id, spec
        )
        version_number = version.version
        definition = service.registry.get(request.tenant_id, agent_id)
    elif not metadata:
        raise ValueError("Agent update contains no changes")

    _emit_agent_event(service, request, "app_updated", definition, version_number)
    return _agent_result(UPDATE_AGENT_DRAFT_TOOL, definition, version_number)


def _emit_agent_event(
    service: AgentRegistryService,
    request: ToolGatewayRequest,
    event_type: str,
    definition: Any,
    version: int,
) -> None:
    run = service.store.get_run(request.tenant_id, request.run_id)
    service.store.append_run_event(
        run,
        event_type,
        {
            "agentId": definition.id,
            "name": definition.name,
            "appKind": definition.app_kind,
            "version": version,
            "status": definition.status,
        },
    )


def _agent_result(
    tool_name: str,
    definition: Any,
    version: int,
) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        output={
            "agent_id": definition.id,
            "version": version,
            "status": definition.status,
            "app_kind": definition.app_kind,
            "write_autonomy": definition.write_autonomy,
            "next_step": "Review and publish the draft from Agent Brain when ready.",
        },
    )


def _agent_tool_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "agent_id",
            "version",
            "status",
            "app_kind",
            "write_autonomy",
            "next_step",
        ],
        "properties": {
            "agent_id": {"type": "string"},
            "version": {"type": "integer"},
            "status": {"type": "string"},
            "app_kind": {"type": "string"},
            "write_autonomy": {"type": "string"},
            "next_step": {"type": "string"},
        },
    }
