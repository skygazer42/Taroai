from collections import Counter
from datetime import datetime
from enum import Enum
from typing import List

from pydantic import BaseModel, Field

from taroai.skills.manifest import SkillManifest, SkillVisibility
from taroai.store import NotFoundError, TenantAccessError
from taroai.domain import utc_now


class SkillStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"


class SkillInstallationStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class SkillRegistryEntry(BaseModel):
    tenant_id: str
    manifest: SkillManifest
    status: SkillStatus = SkillStatus.DRAFT
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SkillInstallation(BaseModel):
    tenant_id: str
    workspace_id: str
    skill_id: str
    status: SkillInstallationStatus = SkillInstallationStatus.ENABLED
    installed_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SkillMarketplaceAnalytics(BaseModel):
    tenant_id: str
    total_skills: int = 0
    total_versions: int = 0
    total_installations: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    visibility_counts: dict[str, int] = Field(default_factory=dict)
    installations_by_workspace: dict[str, int] = Field(default_factory=dict)


def is_skill_entry_visible(
    entry: SkillRegistryEntry,
    user_id: str,
    workspace_id: str | None = None,
    department_id: str | None = None,
) -> bool:
    visibility = entry.manifest.visibility
    if visibility == SkillVisibility.TENANT:
        return True
    if visibility == SkillVisibility.DEPARTMENT:
        return department_id in entry.manifest.visible_to_department_ids
    if visibility == SkillVisibility.WORKSPACE:
        return workspace_id in entry.manifest.visible_to_workspace_ids
    if visibility == SkillVisibility.PRIVATE:
        return (
            user_id == entry.created_by_user_id
            or user_id in entry.manifest.visible_to_user_ids
        )
    return False


def build_skill_marketplace_analytics(
    tenant_id: str,
    entries: List[SkillRegistryEntry],
    total_versions: int,
    installations: List[SkillInstallation],
) -> SkillMarketplaceAnalytics:
    status_counts = Counter(entry.status.value for entry in entries)
    visibility_counts = Counter(entry.manifest.visibility.value for entry in entries)
    installations_by_workspace = Counter(
        installation.workspace_id for installation in installations
    )
    return SkillMarketplaceAnalytics(
        tenant_id=tenant_id,
        total_skills=len(entries),
        total_versions=total_versions,
        total_installations=len(installations),
        status_counts=dict(sorted(status_counts.items())),
        visibility_counts=dict(sorted(visibility_counts.items())),
        installations_by_workspace=dict(sorted(installations_by_workspace.items())),
    )


class InMemorySkillRegistry(BaseModel):
    manifests: dict[str, SkillManifest] = Field(default_factory=dict)
    entries: dict[str, SkillRegistryEntry] = Field(default_factory=dict)
    version_entries: dict[str, list[SkillRegistryEntry]] = Field(default_factory=dict)
    installations: dict[str, SkillInstallation] = Field(default_factory=dict)

    def register(self, manifest: SkillManifest) -> SkillManifest:
        self.manifests[manifest.id] = manifest
        return manifest

    def get(self, skill_id: str) -> SkillManifest:
        manifest = self.manifests.get(skill_id)
        if manifest is None:
            raise NotFoundError(f"Skill not found: {skill_id}")
        return manifest

    def list(self) -> list[SkillManifest]:
        return list(self.manifests.values())

    def register_for_tenant(
        self,
        tenant_id: str,
        created_by_user_id: str,
        manifest: SkillManifest,
    ) -> SkillRegistryEntry:
        key = self._tenant_skill_key(tenant_id, manifest.id)
        existing = self.entries.get(key)
        if any(
            entry.manifest.version == manifest.version
            for entry in self.version_entries.get(key, [])
        ):
            raise ValueError(f"Skill already exists: {manifest.id}@{manifest.version}")
        now = utc_now()
        entry = SkillRegistryEntry(
            tenant_id=tenant_id,
            manifest=manifest,
            created_by_user_id=created_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.entries[key] = entry
        self.version_entries.setdefault(key, []).append(
            SkillRegistryEntry(
                tenant_id=tenant_id,
                manifest=manifest,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
        )
        return entry

    def get_for_tenant(self, tenant_id: str, skill_id: str) -> SkillRegistryEntry:
        entry = self.entries.get(self._tenant_skill_key(tenant_id, skill_id))
        if entry is None:
            raise NotFoundError(f"Skill not found: {skill_id}")
        if entry.tenant_id != tenant_id:
            raise TenantAccessError(f"Skill {skill_id} is not in tenant {tenant_id}")
        return entry

    def get_visible_for_tenant(
        self,
        tenant_id: str,
        skill_id: str,
        user_id: str,
        workspace_id: str | None = None,
        department_id: str | None = None,
    ) -> SkillRegistryEntry:
        entry = self.get_for_tenant(tenant_id, skill_id)
        if not is_skill_entry_visible(entry, user_id, workspace_id, department_id):
            raise NotFoundError(f"Skill not found: {skill_id}")
        return entry

    def list_for_tenant(self, tenant_id: str) -> List[SkillRegistryEntry]:
        return [
            entry
            for entry in self.entries.values()
            if entry.tenant_id == tenant_id
        ]

    def list_visible_for_tenant(
        self,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None = None,
        department_id: str | None = None,
    ) -> List[SkillRegistryEntry]:
        return [
            entry
            for entry in self.list_for_tenant(tenant_id)
            if is_skill_entry_visible(entry, user_id, workspace_id, department_id)
        ]

    def get_marketplace_analytics(self, tenant_id: str) -> SkillMarketplaceAnalytics:
        entries = self.list_for_tenant(tenant_id)
        installations = [
            installation
            for installation in self.installations.values()
            if installation.tenant_id == tenant_id
        ]
        total_versions = sum(
            len(version_entries)
            for key, version_entries in self.version_entries.items()
            if key.startswith(f"{tenant_id}:")
        )
        return build_skill_marketplace_analytics(
            tenant_id,
            entries,
            total_versions,
            installations,
        )

    def list_versions(self, tenant_id: str, skill_id: str) -> List[SkillRegistryEntry]:
        return [
            entry
            for entry in self.version_entries.get(self._tenant_skill_key(tenant_id, skill_id), [])
            if entry.tenant_id == tenant_id
        ]

    def publish(self, tenant_id: str, skill_id: str) -> SkillRegistryEntry:
        return self._update_status(tenant_id, skill_id, SkillStatus.PUBLISHED)

    def disable(self, tenant_id: str, skill_id: str) -> SkillRegistryEntry:
        return self._update_status(tenant_id, skill_id, SkillStatus.DISABLED)

    def install_for_workspace(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        installed_by_user_id: str,
    ) -> SkillInstallation:
        entry = self.get_for_tenant(tenant_id, skill_id)
        if entry.status != SkillStatus.PUBLISHED:
            raise ValueError(f"Skill is not published: {skill_id}")
        key = self._tenant_workspace_skill_key(tenant_id, workspace_id, skill_id)
        existing = self.installations.get(key)
        installation = SkillInstallation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            installed_by_user_id=installed_by_user_id,
            created_at=existing.created_at if existing is not None else utc_now(),
        )
        self.installations[key] = installation
        return installation

    def list_for_workspace(self, tenant_id: str, workspace_id: str) -> List[SkillInstallation]:
        return [
            installation
            for installation in self.installations.values()
            if installation.tenant_id == tenant_id and installation.workspace_id == workspace_id
        ]

    def enable_for_workspace(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillInstallation:
        return self._update_installation_status(
            tenant_id,
            workspace_id,
            skill_id,
            SkillInstallationStatus.ENABLED,
        )

    def disable_for_workspace(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillInstallation:
        return self._update_installation_status(
            tenant_id,
            workspace_id,
            skill_id,
            SkillInstallationStatus.DISABLED,
        )

    def _update_status(
        self,
        tenant_id: str,
        skill_id: str,
        status: SkillStatus,
    ) -> SkillRegistryEntry:
        entry = self.get_for_tenant(tenant_id, skill_id)
        updated = entry.model_copy(update={"status": status, "updated_at": utc_now()})
        self.entries[self._tenant_skill_key(tenant_id, skill_id)] = updated
        return updated

    def _update_installation_status(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        status: SkillInstallationStatus,
    ) -> SkillInstallation:
        key = self._tenant_workspace_skill_key(tenant_id, workspace_id, skill_id)
        installation = self.installations.get(key)
        if installation is None:
            raise NotFoundError(f"Skill installation not found: {skill_id}")
        updated = installation.model_copy(update={"status": status, "updated_at": utc_now()})
        self.installations[key] = updated
        return updated

    def _tenant_skill_key(self, tenant_id: str, skill_id: str) -> str:
        return f"{tenant_id}:{skill_id}"

    def _tenant_workspace_skill_key(self, tenant_id: str, workspace_id: str, skill_id: str) -> str:
        return f"{tenant_id}:{workspace_id}:{skill_id}"
