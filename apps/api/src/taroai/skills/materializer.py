from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.skills.package import (
    SkillPackage,
    SkillPackageFileKind,
    normalize_package_path,
)


class SandboxSkillWrite(BaseModel):
    path: str = Field(min_length=1)
    content: bytes = Field(repr=False, exclude=True)
    content_digest: str
    size_bytes: int = Field(ge=0)
    mode: int = Field(default=0o440, ge=0, le=0o777)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_workspace_path(self) -> "SandboxSkillWrite":
        if not self.path.startswith("/workspace/.taroai/skills/"):
            raise ValueError("skill materialization writes must stay under the sandbox skill root")
        if self.size_bytes != len(self.content):
            raise ValueError("sandbox write size does not match content")
        return self


class SkillMaterializationPlan(BaseModel):
    skill_id: str
    version: str
    root_path: str
    package_digest: str
    source_digest: str
    runtime_sandbox: str
    timeout_seconds: int = Field(ge=1)
    resolved_dependencies: list[dict] = Field(default_factory=list)
    writes: tuple[SandboxSkillWrite, ...]

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillMaterializer:
    """Builds sandbox write intents only; it never writes or executes on the host."""

    sandbox_root = "/workspace/.taroai/skills"

    def plan(self, package: SkillPackage) -> SkillMaterializationPlan:
        # Package validation guarantees these are safe single path segments.
        skill_id = normalize_package_path(package.skill_id)
        version = normalize_package_path(package.version)
        if "/" in skill_id or "/" in version:
            raise ValueError("skill id and version must each be one sandbox path segment")
        root = f"{self.sandbox_root}/{skill_id}/{version}/"
        writes = tuple(
            SandboxSkillWrite(
                path=f"{root}{item.path}",
                content=item.content,
                content_digest=item.content_digest,
                size_bytes=item.size_bytes,
                mode=(
                    0o550
                    if item.kind == SkillPackageFileKind.SCRIPT
                    else 0o440
                ),
            )
            for item in sorted(package.files, key=lambda value: value.path)
        )
        return SkillMaterializationPlan(
            skill_id=package.skill_id,
            version=package.version,
            root_path=root,
            package_digest=package.package_digest,
            source_digest=package.provenance.source_digest,
            runtime_sandbox=package.manifest.runtime.sandbox,
            timeout_seconds=package.manifest.runtime.timeout_seconds,
            resolved_dependencies=[
                item.model_dump(mode="json")
                for item in package.resolved_dependencies
            ],
            writes=writes,
        )

