from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from taroai.skills.package import SkillPackage, SkillPackageKind


class SkillDiscoveryRegistry(Protocol):
    def list_discoverable_packages(
        self,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        department_id: str | None = None,
    ) -> list[SkillPackage]: ...

    def get_installed_package(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillPackage: ...


class SkillDiscoverySummary(BaseModel):
    skill_id: str
    version: str
    name: str
    description: str
    package_digest: str
    source_digest: str
    input_schema: dict[str, Any]
    allowed_tools: list[str] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)
    risk_level: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillLoadedContent(BaseModel):
    skill_id: str
    version: str
    package_digest: str
    source_digest: str
    source_type: str
    source_url: str | None = None
    source_ref: str | None = None
    skill_md: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillDiscoveryService:
    """Exposes compact summaries first and loads SKILL.md only after selection."""

    def __init__(self, registry: SkillDiscoveryRegistry):
        self.registry = registry

    def discover(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        department_id: str | None = None,
    ) -> list[SkillDiscoverySummary]:
        packages = self.registry.list_discoverable_packages(
            tenant_id,
            workspace_id,
            user_id,
            department_id,
        )
        return [
            SkillDiscoverySummary(
                skill_id=package.skill_id,
                version=package.version,
                name=package.manifest.name,
                description=package.manifest.description,
                package_digest=package.package_digest,
                source_digest=package.provenance.source_digest,
                input_schema=package.manifest.input_schema,
                allowed_tools=_requirement_ids(
                    package.taroai_config.get("spec", {}).get("tools", [])
                ),
                required_scopes=list(package.manifest.required_scopes),
                risk_level=package.manifest.risk_level,
            )
            for package in sorted(packages, key=lambda item: item.skill_id)
            if package.package_kind == SkillPackageKind.PACKAGE
        ]

    def load_skill(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        expected_version: str | None = None,
        expected_package_digest: str | None = None,
        expected_source_digest: str | None = None,
    ) -> SkillLoadedContent:
        package = self.registry.get_installed_package(
            tenant_id,
            workspace_id,
            skill_id,
        )
        if package.package_kind != SkillPackageKind.PACKAGE:
            raise ValueError(
                "legacy manifest skills cannot be loaded through discovery"
            )
        if expected_version is not None and package.version != expected_version:
            raise ValueError("installed skill version changed before load")
        if (
            expected_package_digest is not None
            and package.package_digest != expected_package_digest
        ):
            raise ValueError("installed skill package digest changed before load")
        if (
            expected_source_digest is not None
            and package.provenance.source_digest != expected_source_digest
        ):
            raise ValueError("installed skill source digest changed before load")
        return SkillLoadedContent(
            skill_id=package.skill_id,
            version=package.version,
            package_digest=package.package_digest,
            source_digest=package.provenance.source_digest,
            source_type=package.provenance.source_type.value,
            source_url=package.provenance.source_url,
            source_ref=package.provenance.source_ref,
            skill_md=package.skill_md,
        )


def _requirement_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [
        str(item.get("id") or item.get("name")) if isinstance(item, dict) else str(item)
        for item in values
        if (isinstance(item, str) and item)
        or (isinstance(item, dict) and (item.get("id") or item.get("name")))
    ]
