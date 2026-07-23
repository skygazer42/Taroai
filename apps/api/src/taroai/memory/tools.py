from datetime import timedelta
import re
from typing import Any

from taroai.domain import utc_now
from taroai.knowledge import retrieval_terms
from taroai.memory.models import MemoryScopeType, MemoryWriteRequest
from taroai.tool_gateway import (
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
)


MEMORY_SAVE_TOOL = "memory.save"


def register_memory_tool_handler(
    gateway: ToolGateway,
    memory_service: Any,
    store: Any | None = None,
) -> None:
    gateway.register_tool(
        ToolPolicy(
            tool_name=MEMORY_SAVE_TOOL,
            description=(
                "Save or update a stable personal fact or preference only when the user "
                "explicitly asks Taroai to remember it. Reuse the same memory_key when "
                "the fact changes, and supersede visible outdated memories for that fact."
            ),
            required_scopes=["memory.write"],
            risk_level=ToolRiskLevel.MEDIUM,
            approval_required=True,
            input_schema={
                "type": "object",
                "required": ["content", "memory_key"],
                "properties": {
                    "content": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4000,
                        "description": "A self-contained sentence in the user's language that names the fact or preference and its value; never save only the value.",
                    },
                    "memory_key": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                        "pattern": "^[a-z0-9_.-]+$",
                        "description": "Specific stable dotted key for this fact, for example profile.response_style or profile.demo_code; never use a generic key such as memory, fact, or legacy.",
                    },
                    "supersedes_memory_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "maxItems": 20,
                        "uniqueItems": True,
                        "description": "Visible memory IDs containing outdated values for this same fact.",
                    },
                    "expires_in_days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3650,
                        "description": "Optional hard expiry requested by the user.",
                    },
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": ["memory_id", "status", "scope_type", "scope_id"],
                "properties": {
                    "memory_id": {"type": "string"},
                    "status": {"type": "string"},
                    "scope_type": {"type": "string"},
                    "scope_id": {"type": "string"},
                    "expires_at": {"type": ["string", "null"]},
                    "memory_key": {"type": "string"},
                    "superseded_memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        ),
        lambda request: _save_user_memory(memory_service, request, store),
    )


def _save_user_memory(
    memory_service: Any,
    request: ToolGatewayRequest,
    store: Any | None = None,
) -> ToolResult:
    content = str(request.tool_input["content"]).strip()
    memory_key = str(request.tool_input["memory_key"]).strip()
    if not content:
        raise ValueError("Memory content cannot be blank")
    if (
        re.fullmatch(r"[a-z0-9_.-]{1,120}", memory_key) is None
        or memory_key in {"fact", "general", "legacy", "memory"}
    ):
        raise ValueError(
            "memory_key must identify the specific fact, for example profile.demo_code"
        )
    active = memory_service.list_by_scope(
        request.tenant_id,
        MemoryScopeType.USER,
        request.user_id,
    )
    active_by_id = {record.id: record for record in active}
    requested_superseded_ids = {
        str(memory_id)
        for memory_id in request.tool_input.get("supersedes_memory_ids", [])
    }
    unknown_ids = requested_superseded_ids - active_by_id.keys()
    if unknown_ids:
        raise ValueError("Can only supersede active memories owned by the current user")
    expires_in_days = request.tool_input.get("expires_in_days")
    expires_at = (
        utc_now() + timedelta(days=int(expires_in_days))
        if expires_in_days is not None
        else None
    )
    indexed_terms = (
        sorted(
            retrieval_terms(store.get_run(request.tenant_id, request.run_id).message)
        )[:256]
        if store is not None
        else []
    )
    memory = memory_service.write(
        MemoryWriteRequest(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            scope_type=MemoryScopeType.USER,
            scope_id=request.user_id,
            source_run_id=request.run_id,
            content=content,
            created_by=request.user_id,
            metadata={
                "source": "explicit_agent_save",
                "memory_key": memory_key,
                **({"retrieval_terms": indexed_terms} if indexed_terms else {}),
            },
            expires_at=expires_at,
        )
    )
    superseded_ids = sorted(
        record.id
        for record in active
        if record.id in requested_superseded_ids
        or record.metadata.get("memory_key") == memory_key
    )
    for memory_id in superseded_ids:
        memory_service.forget(request.tenant_id, memory_id)
    return ToolResult(
        tool_name=MEMORY_SAVE_TOOL,
        output={
            "memory_id": memory.id,
            "status": memory.status.value,
            "scope_type": memory.scope_type.value,
            "scope_id": memory.scope_id,
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
            "memory_key": memory_key,
            "superseded_memory_ids": superseded_ids,
        },
    )
