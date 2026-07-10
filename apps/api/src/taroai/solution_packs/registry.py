from pydantic import BaseModel, Field

from taroai.domain import utc_now
from taroai.solution_packs.models import (
    SolutionPackEntry,
    SolutionPackInstallation,
    SolutionPackInstallationStatus,
    SolutionPackManifest,
    SolutionPackStatus,
)
from taroai.store import NotFoundError


class InMemorySolutionPackRegistry(BaseModel):
    entries: dict[str, SolutionPackEntry] = Field(default_factory=dict)
    version_entries: dict[str, list[SolutionPackEntry]] = Field(default_factory=dict)
    installations: dict[str, SolutionPackInstallation] = Field(default_factory=dict)

    def register_for_tenant(
        self,
        tenant_id: str,
        created_by_user_id: str,
        manifest: SolutionPackManifest,
    ) -> SolutionPackEntry:
        key = self._tenant_pack_key(tenant_id, manifest.id)
        existing = self.entries.get(key)
        if any(
            entry.manifest.version == manifest.version
            for entry in self.version_entries.get(key, [])
        ):
            raise ValueError(f"Solution pack already exists: {manifest.id}@{manifest.version}")
        now = utc_now()
        entry = SolutionPackEntry(
            tenant_id=tenant_id,
            manifest=manifest,
            created_by_user_id=created_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.entries[key] = entry
        self.version_entries.setdefault(key, []).append(
            SolutionPackEntry(
                tenant_id=tenant_id,
                manifest=manifest,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
        )
        return entry

    def get_for_tenant(self, tenant_id: str, pack_id: str) -> SolutionPackEntry:
        entry = self.entries.get(self._tenant_pack_key(tenant_id, pack_id))
        if entry is None:
            raise NotFoundError(f"Solution pack not found: {pack_id}")
        return entry

    def list_for_tenant(self, tenant_id: str) -> list[SolutionPackEntry]:
        return [
            entry
            for entry in self.entries.values()
            if entry.tenant_id == tenant_id
        ]

    def list_versions(self, tenant_id: str, pack_id: str) -> list[SolutionPackEntry]:
        return [
            entry
            for entry in self.version_entries.get(self._tenant_pack_key(tenant_id, pack_id), [])
            if entry.tenant_id == tenant_id
        ]

    def publish(self, tenant_id: str, pack_id: str) -> SolutionPackEntry:
        return self._update_status(tenant_id, pack_id, SolutionPackStatus.PUBLISHED)

    def disable(self, tenant_id: str, pack_id: str) -> SolutionPackEntry:
        return self._update_status(tenant_id, pack_id, SolutionPackStatus.DISABLED)

    def record_installation(
        self,
        tenant_id: str,
        pack_id: str,
        version: str,
        workspace_ids: list[str],
        installed_skill_ids: list[str],
        installed_by_user_id: str,
    ) -> SolutionPackInstallation:
        key = self._tenant_pack_key(tenant_id, pack_id)
        existing = self.installations.get(key)
        now = utc_now()
        installation = SolutionPackInstallation(
            tenant_id=tenant_id,
            pack_id=pack_id,
            version=version,
            workspace_ids=workspace_ids,
            installed_skill_ids=installed_skill_ids,
            installed_by_user_id=installed_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.installations[key] = installation
        return installation

    def get_installation(self, tenant_id: str, pack_id: str) -> SolutionPackInstallation:
        installation = self.installations.get(self._tenant_pack_key(tenant_id, pack_id))
        if installation is None:
            raise NotFoundError(f"Solution pack installation not found: {pack_id}")
        return installation

    def list_installations(self, tenant_id: str) -> list[SolutionPackInstallation]:
        return [
            installation
            for installation in self.installations.values()
            if installation.tenant_id == tenant_id
        ]

    def update_installation_status(
        self,
        tenant_id: str,
        pack_id: str,
        status: SolutionPackInstallationStatus,
    ) -> SolutionPackInstallation:
        installation = self.get_installation(tenant_id, pack_id)
        updated = installation.model_copy(update={"status": status, "updated_at": utc_now()})
        self.installations[self._tenant_pack_key(tenant_id, pack_id)] = updated
        return updated

    def _update_status(
        self,
        tenant_id: str,
        pack_id: str,
        status: SolutionPackStatus,
    ) -> SolutionPackEntry:
        entry = self.get_for_tenant(tenant_id, pack_id)
        updated = entry.model_copy(update={"status": status, "updated_at": utc_now()})
        self.entries[self._tenant_pack_key(tenant_id, pack_id)] = updated
        return updated

    def _tenant_pack_key(self, tenant_id: str, pack_id: str) -> str:
        return f"{tenant_id}:{pack_id}"
