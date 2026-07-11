import io
import json
import re
import zipfile
from typing import Any

from taroai.skills.service import SkillService
from taroai.tool_gateway import (
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
)


CREATE_SKILL_DRAFT_TOOL = "skill.package.create_draft"


def register_skill_tool_handlers(
    gateway: ToolGateway,
    skill_service: SkillService,
) -> None:
    gateway.register_tool(
        ToolPolicy(
            tool_name=CREATE_SKILL_DRAFT_TOOL,
            required_scopes=["skills.publish"],
            risk_level=ToolRiskLevel.MEDIUM,
            input_schema={
                "type": "object",
                "required": ["name", "description", "instructions"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "description": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4000,
                    },
                    "instructions": {"type": "string", "minLength": 1},
                    "version": {"type": "string", "default": "1.0.0"},
                    "supporting_files": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": ["skill_id", "version", "package_digest", "status"],
                "properties": {
                    "skill_id": {"type": "string"},
                    "version": {"type": "string"},
                    "package_digest": {"type": "string"},
                    "status": {"type": "string"},
                    "file_count": {"type": "integer"},
                    "next_step": {"type": "string"},
                },
            },
        ),
        lambda request: _create_skill_draft(skill_service, request),
    )


def _create_skill_draft(
    skill_service: SkillService,
    request: ToolGatewayRequest,
) -> ToolResult:
    name = str(request.tool_input["name"]).strip()
    description = str(request.tool_input["description"]).strip()
    instructions = str(request.tool_input["instructions"]).strip()
    version = str(request.tool_input.get("version") or "1.0.0").strip()
    supporting_files = dict(request.tool_input.get("supporting_files") or {})
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", name):
        raise ValueError(
            "skill name must contain only letters, numbers, dots, underscores, and hyphens"
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,99}", version):
        raise ValueError("skill version is invalid")
    skill_md = (
        "---\n"
        f"name: {json.dumps(name, ensure_ascii=False)}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        f"metadata: {json.dumps({'version': version}, ensure_ascii=False)}\n"
        "---\n\n"
        f"# {name}\n\n"
        f"{instructions}\n"
    )
    package = skill_service.import_zip(
        tenant_id=request.tenant_id,
        created_by_user_id=request.user_id,
        archive_bytes=_skill_archive(skill_md, supporting_files),
        source_ref=f"chat-run:{request.run_id}",
    )
    return ToolResult(
        tool_name=CREATE_SKILL_DRAFT_TOOL,
        output={
            "skill_id": package.skill_id,
            "version": package.version,
            "package_digest": package.package_digest,
            "status": "draft",
            "file_count": len(package.files),
            "next_step": (
                "Review the package in Agent Brain, run its evaluation suite, "
                "then publish and install it in the workspace."
            ),
        },
    )


def _skill_archive(skill_md: str, supporting_files: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", skill_md)
        for path, content in sorted(supporting_files.items()):
            if path == "SKILL.md":
                raise ValueError("supporting_files cannot replace SKILL.md")
            archive.writestr(str(path), str(content))
    return buffer.getvalue()
