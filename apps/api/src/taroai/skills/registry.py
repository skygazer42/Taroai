from collections import Counter
from datetime import datetime
from enum import Enum
from typing import Any, List

from pydantic import BaseModel, Field

from taroai.skills.evaluation import SkillEvaluationRun
from taroai.skills.manifest import SkillManifest, SkillVisibility
from taroai.skills.package import SkillPackage, SkillPackageKind
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


class SkillPackageRecord(BaseModel):
    tenant_id: str
    package: SkillPackage
    status: SkillStatus = SkillStatus.DRAFT
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class SkillInstallation(BaseModel):
    tenant_id: str
    workspace_id: str
    skill_id: str
    status: SkillInstallationStatus = SkillInstallationStatus.ENABLED
    installed_by_user_id: str
    installed_version: str | None = None
    package_digest: str | None = None
    source_digest: str | None = None
    resolved_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    package_kind: SkillPackageKind = SkillPackageKind.LEGACY_MANIFEST
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
    version_entries: dict[str, List[SkillRegistryEntry]] = Field(default_factory=dict)
    installations: dict[str, SkillInstallation] = Field(default_factory=dict)
    packages: dict[str, SkillPackageRecord] = Field(default_factory=dict)
    evaluation_runs: dict[str, SkillEvaluationRun] = Field(default_factory=dict)

    def register(self, manifest: SkillManifest) -> SkillManifest:
        self.manifests[manifest.id] = manifest
        return manifest

    def get(self, skill_id: str) -> SkillManifest:
        manifest = self.manifests.get(skill_id)
        if manifest is None:
            raise NotFoundError(f"Skill not found: {skill_id}")
        return manifest

    def list(self) -> List[SkillManifest]:
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

    def register_package_for_tenant(
        self,
        tenant_id: str,
        created_by_user_id: str,
        package: SkillPackage,
    ) -> SkillPackageRecord:
        package_key = self._tenant_package_key(
            tenant_id,
            package.skill_id,
            package.version,
        )
        if package_key in self.packages:
            raise ValueError(
                f"Skill package already exists: {package.skill_id}@{package.version}"
            )
        version_entry = next(
            (
                item
                for item in self.list_versions(tenant_id, package.skill_id)
                if item.manifest.version == package.version
            ),
            None,
        )
        if version_entry is None:
            self.register_for_tenant(
                tenant_id,
                created_by_user_id,
                package.manifest,
            )
        elif version_entry.manifest != package.manifest:
            raise ValueError("immutable skill version already has a different manifest")
        now = utc_now()
        record = SkillPackageRecord(
            tenant_id=tenant_id,
            package=package,
            created_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=now,
        )
        self.packages[package_key] = record
        return record

    def get_package_record(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillPackageRecord:
        record = self.packages.get(
            self._tenant_package_key(tenant_id, skill_id, version)
        )
        if record is None:
            raise NotFoundError(f"Skill package not found: {skill_id}@{version}")
        return record

    def get_package_version(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillPackage:
        return self.get_package_record(tenant_id, skill_id, version).package

    def list_package_records(
        self,
        tenant_id: str,
        skill_id: str | None = None,
    ) -> List[SkillPackageRecord]:
        return sorted(
            [
                record
                for record in self.packages.values()
                if record.tenant_id == tenant_id
                and (skill_id is None or record.package.skill_id == skill_id)
            ],
            key=lambda record: (
                record.package.skill_id,
                record.created_at,
                record.package.version,
            ),
        )

    def publish_package(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillPackageRecord:
        return self._update_package_status(
            tenant_id,
            skill_id,
            version,
            SkillStatus.PUBLISHED,
        )

    def disable_package(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillPackageRecord:
        return self._update_package_status(
            tenant_id,
            skill_id,
            version,
            SkillStatus.DISABLED,
        )

    def publish(self, tenant_id: str, skill_id: str) -> SkillRegistryEntry:
        current = self.get_for_tenant(tenant_id, skill_id)
        package_key = self._tenant_package_key(
            tenant_id,
            skill_id,
            current.manifest.version,
        )
        if package_key in self.packages:
            self.publish_package(tenant_id, skill_id, current.manifest.version)
            return self.get_for_tenant(tenant_id, skill_id)
        return self._update_status(tenant_id, skill_id, SkillStatus.PUBLISHED)

    def disable(self, tenant_id: str, skill_id: str) -> SkillRegistryEntry:
        current = self.get_for_tenant(tenant_id, skill_id)
        package_key = self._tenant_package_key(
            tenant_id,
            skill_id,
            current.manifest.version,
        )
        if package_key in self.packages:
            self.disable_package(tenant_id, skill_id, current.manifest.version)
            return self.get_for_tenant(tenant_id, skill_id)
        return self._update_status(tenant_id, skill_id, SkillStatus.DISABLED)

    def install_for_workspace(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        installed_by_user_id: str,
        *,
        version: str | None = None,
        package_digest: str | None = None,
    ) -> SkillInstallation:
        entry = self.get_for_tenant(tenant_id, skill_id)
        target_version = version or entry.manifest.version
        package_record = self.packages.get(
            self._tenant_package_key(tenant_id, skill_id, target_version)
        )
        if package_record is not None:
            if package_record.status != SkillStatus.PUBLISHED:
                raise ValueError(f"Skill package is not published: {skill_id}@{target_version}")
            if (
                package_digest is not None
                and package_record.package.package_digest != package_digest
            ):
                raise ValueError("requested package digest does not match published package")
            return self._install_package_version(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                installed_by_user_id=installed_by_user_id,
                package_record=package_record,
            )
        if version is not None or package_digest is not None:
            raise NotFoundError(f"Skill package not found: {skill_id}@{target_version}")
        if entry.status != SkillStatus.PUBLISHED:
            raise ValueError(f"Skill is not published: {skill_id}")
        key = self._tenant_workspace_skill_key(tenant_id, workspace_id, skill_id)
        existing = self.installations.get(key)
        installation = SkillInstallation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            installed_by_user_id=installed_by_user_id,
            installed_version=entry.manifest.version,
            package_kind=SkillPackageKind.LEGACY_MANIFEST,
            created_at=existing.created_at if existing is not None else utc_now(),
        )
        self.installations[key] = installation
        return installation

    def upgrade_for_workspace(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        target_version: str,
        updated_by_user_id: str,
        *,
        expected_package_digest: str | None = None,
    ) -> SkillInstallation:
        current = self.get_installation(tenant_id, workspace_id, skill_id)
        if (
            expected_package_digest is not None
            and current.package_digest != expected_package_digest
        ):
            raise ValueError("workspace skill installation changed before upgrade")
        record = self.get_package_record(tenant_id, skill_id, target_version)
        if record.status != SkillStatus.PUBLISHED:
            raise ValueError(f"Skill package is not published: {skill_id}@{target_version}")
        return self._install_package_version(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            installed_by_user_id=updated_by_user_id,
            package_record=record,
        )

    def rollback_for_workspace(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        target_version: str,
        rolled_back_by_user_id: str,
        *,
        expected_package_digest: str | None = None,
    ) -> SkillInstallation:
        return self.upgrade_for_workspace(
            tenant_id,
            workspace_id,
            skill_id,
            target_version,
            rolled_back_by_user_id,
            expected_package_digest=expected_package_digest,
        )

    def get_installation(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillInstallation:
        installation = self.installations.get(
            self._tenant_workspace_skill_key(tenant_id, workspace_id, skill_id)
        )
        if installation is None:
            raise NotFoundError(f"Skill installation not found: {skill_id}")
        return installation

    def get_installed_package(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillPackage:
        installation = self.get_installation(tenant_id, workspace_id, skill_id)
        return self._resolve_complete_package_pin(installation)

    def list_discoverable_packages(
        self,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        department_id: str | None = None,
    ) -> List[SkillPackage]:
        discovered: List[SkillPackage] = []
        for installation in self.list_for_workspace(tenant_id, workspace_id):
            if installation.status != SkillInstallationStatus.ENABLED:
                continue
            try:
                package = self._resolve_complete_package_pin(installation)
                record = self.get_package_record(
                    tenant_id,
                    package.skill_id,
                    package.version,
                )
            except (NotFoundError, ValueError):
                continue
            visible_entry = SkillRegistryEntry(
                tenant_id=tenant_id,
                manifest=package.manifest,
                status=record.status,
                created_by_user_id=record.created_by_user_id,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            if is_skill_entry_visible(
                visible_entry,
                user_id,
                workspace_id,
                department_id,
            ):
                discovered.append(package)
        return discovered

    def record_evaluation_run(
        self,
        run: SkillEvaluationRun,
    ) -> SkillEvaluationRun:
        if run.id in self.evaluation_runs:
            raise ValueError(f"Skill evaluation run already exists: {run.id}")
        package = self.get_package_version(run.tenant_id, run.skill_id, run.version)
        if package.package_digest != run.package_digest:
            raise ValueError("evaluation run package digest does not match immutable package")
        self.evaluation_runs[run.id] = run
        return run

    def list_evaluation_runs(
        self,
        tenant_id: str,
        skill_id: str,
        version: str | None = None,
    ) -> List[SkillEvaluationRun]:
        return sorted(
            [
                run
                for run in self.evaluation_runs.values()
                if run.tenant_id == tenant_id
                and run.skill_id == skill_id
                and (version is None or run.version == version)
            ],
            key=lambda run: run.created_at,
        )

    def latest_evaluation_run(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillEvaluationRun:
        runs = self.list_evaluation_runs(tenant_id, skill_id, version)
        if not runs:
            raise NotFoundError(
                f"Skill evaluation run not found: {skill_id}@{version}"
            )
        return runs[-1]

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

    def _update_package_status(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
        status: SkillStatus,
    ) -> SkillPackageRecord:
        key = self._tenant_package_key(tenant_id, skill_id, version)
        record = self.get_package_record(tenant_id, skill_id, version)
        now = utc_now()
        updated_record = record.model_copy(
            update={
                "status": status,
                "published_at": (
                    now if status == SkillStatus.PUBLISHED else record.published_at
                ),
                "updated_at": now,
            }
        )
        self.packages[key] = updated_record
        version_key = self._tenant_skill_key(tenant_id, skill_id)
        self.version_entries[version_key] = [
            item.model_copy(update={"status": status, "updated_at": now})
            if item.manifest.version == version
            else item
            for item in self.version_entries.get(version_key, [])
        ]
        current = self.entries.get(version_key)
        if current is not None and current.manifest.version == version:
            self.entries[version_key] = current.model_copy(
                update={"status": status, "updated_at": now}
            )
        return updated_record

    def uninstall_for_workspace(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillInstallation:
        key = self._tenant_workspace_skill_key(tenant_id, workspace_id, skill_id)
        installation = self.get_installation(tenant_id, workspace_id, skill_id)
        del self.installations[key]
        return installation

    def _install_package_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        installed_by_user_id: str,
        package_record: SkillPackageRecord,
    ) -> SkillInstallation:
        package = package_record.package
        key = self._tenant_workspace_skill_key(
            tenant_id,
            workspace_id,
            package.skill_id,
        )
        existing = self.installations.get(key)
        now = utc_now()
        installation = SkillInstallation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=package.skill_id,
            status=SkillInstallationStatus.ENABLED,
            installed_by_user_id=installed_by_user_id,
            installed_version=package.version,
            package_digest=package.package_digest,
            source_digest=package.provenance.source_digest,
            resolved_dependencies=[
                dependency.model_dump(mode="json")
                for dependency in package.resolved_dependencies
            ],
            package_kind=SkillPackageKind.PACKAGE,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.installations[key] = installation
        return installation

    def _resolve_complete_package_pin(
        self,
        installation: SkillInstallation,
    ) -> SkillPackage:
        if installation.status != SkillInstallationStatus.ENABLED:
            raise ValueError("skill installation is disabled")
        if installation.package_kind != SkillPackageKind.PACKAGE:
            raise ValueError("legacy manifest skills are excluded from automatic discovery")
        if (
            installation.installed_version is None
            or installation.package_digest is None
            or installation.source_digest is None
        ):
            raise ValueError("skill installation does not contain a complete package pin")
        record = self.get_package_record(
            installation.tenant_id,
            installation.skill_id,
            installation.installed_version,
        )
        package = record.package
        if record.status != SkillStatus.PUBLISHED:
            raise ValueError("installed skill package is not published")
        if package.package_digest != installation.package_digest:
            raise ValueError("installed skill package digest does not match registry")
        if package.provenance.source_digest != installation.source_digest:
            raise ValueError("installed skill source digest does not match registry")
        expected_dependencies = [
            dependency.model_dump(mode="json")
            for dependency in package.resolved_dependencies
        ]
        if installation.resolved_dependencies != expected_dependencies:
            raise ValueError("installed skill dependency pins do not match registry")
        return package

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

    def _tenant_package_key(self, tenant_id: str, skill_id: str, version: str) -> str:
        return f"{tenant_id}:{skill_id}:{version}"
