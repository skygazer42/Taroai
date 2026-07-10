import json
from datetime import datetime

from pydantic import BaseModel

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.skills.manifest import SkillManifest
from taroai.skills.registry import (
    SkillInstallation,
    SkillInstallationStatus,
    SkillMarketplaceAnalytics,
    SkillRegistryEntry,
    SkillStatus,
    build_skill_marketplace_analytics,
    is_skill_entry_visible,
)
from taroai.store import NotFoundError


class SqlSkillRegistry(BaseModel):
    config: DatabaseConfig

    def register_for_tenant(
        self,
        tenant_id: str,
        created_by_user_id: str,
        manifest: SkillManifest,
    ) -> SkillRegistryEntry:
        existing = self._get_optional(tenant_id, manifest.id)
        if self._get_version_optional(tenant_id, manifest.id, manifest.version) is not None:
            raise ValueError(f"Skill already exists: {manifest.id}@{manifest.version}")
        now = utc_now()
        entry = SkillRegistryEntry(
            tenant_id=tenant_id,
            manifest=manifest,
            created_by_user_id=created_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, tenant_id)
            connection.execute(
                """
                INSERT INTO skill_registry_versions (
                    tenant_id, skill_id, version, manifest, status,
                    created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.tenant_id,
                    entry.manifest.id,
                    entry.manifest.version,
                    self._json(entry.manifest.model_dump(mode="json")),
                    entry.status.value,
                    entry.created_by_user_id,
                    self._dt(now),
                    self._dt(now),
                ),
            )
            connection.execute(
                """
                INSERT INTO skill_registry_entries (
                    tenant_id, skill_id, version, manifest, status,
                    created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, skill_id) DO UPDATE SET
                    version = excluded.version,
                    manifest = excluded.manifest,
                    status = excluded.status,
                    created_by_user_id = excluded.created_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    entry.tenant_id,
                    entry.manifest.id,
                    entry.manifest.version,
                    self._json(entry.manifest.model_dump(mode="json")),
                    entry.status.value,
                    entry.created_by_user_id,
                    self._dt(entry.created_at),
                    self._dt(entry.updated_at),
                ),
            )
        return entry

    def get_for_tenant(self, tenant_id: str, skill_id: str) -> SkillRegistryEntry:
        entry = self._get_optional(tenant_id, skill_id)
        if entry is None:
            raise NotFoundError(f"Skill not found: {skill_id}")
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

    def list_for_tenant(self, tenant_id: str) -> list[SkillRegistryEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM skill_registry_entries
                WHERE tenant_id = ?
                ORDER BY updated_at, skill_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def list_visible_for_tenant(
        self,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None = None,
        department_id: str | None = None,
    ) -> list[SkillRegistryEntry]:
        return [
            entry
            for entry in self.list_for_tenant(tenant_id)
            if is_skill_entry_visible(entry, user_id, workspace_id, department_id)
        ]

    def get_marketplace_analytics(self, tenant_id: str) -> SkillMarketplaceAnalytics:
        entries = self.list_for_tenant(tenant_id)
        with self._connect() as connection:
            version_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM skill_registry_versions
                WHERE tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()["count"]
            installation_rows = connection.execute(
                """
                SELECT * FROM skill_installations
                WHERE tenant_id = ?
                ORDER BY updated_at, workspace_id, skill_id
                """,
                (tenant_id,),
            ).fetchall()
        return build_skill_marketplace_analytics(
            tenant_id,
            entries,
            int(version_count),
            [self._installation_from_row(row) for row in installation_rows],
        )

    def list_versions(self, tenant_id: str, skill_id: str) -> list[SkillRegistryEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM skill_registry_versions
                WHERE tenant_id = ? AND skill_id = ?
                ORDER BY created_at, version
                """,
                (tenant_id, skill_id),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

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
        existing = self._get_installation_optional(tenant_id, workspace_id, skill_id)
        now = utc_now()
        installation = SkillInstallation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            installed_by_user_id=installed_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, tenant_id)
            self._ensure_workspace(connection, tenant_id, workspace_id)
            connection.execute(
                """
                INSERT INTO skill_installations (
                    tenant_id, workspace_id, skill_id, status,
                    installed_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, workspace_id, skill_id) DO UPDATE SET
                    status = excluded.status,
                    installed_by_user_id = excluded.installed_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    installation.tenant_id,
                    installation.workspace_id,
                    installation.skill_id,
                    installation.status.value,
                    installation.installed_by_user_id,
                    self._dt(installation.created_at),
                    self._dt(installation.updated_at),
                ),
            )
        return installation

    def list_for_workspace(self, tenant_id: str, workspace_id: str) -> list[SkillInstallation]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM skill_installations
                WHERE tenant_id = ? AND workspace_id = ?
                ORDER BY updated_at, skill_id
                """,
                (tenant_id, workspace_id),
            ).fetchall()
        return [self._installation_from_row(row) for row in rows]

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
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE skill_registry_entries
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND skill_id = ?
                """,
                (
                    updated.status.value,
                    self._dt(updated.updated_at),
                    tenant_id,
                    skill_id,
                ),
            )
        return updated

    def _get_optional(self, tenant_id: str, skill_id: str) -> SkillRegistryEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM skill_registry_entries
                WHERE tenant_id = ? AND skill_id = ?
                """,
                (tenant_id, skill_id),
            ).fetchone()
        if row is None:
            return None
        return self._entry_from_row(row)

    def _get_version_optional(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillRegistryEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM skill_registry_versions
                WHERE tenant_id = ? AND skill_id = ? AND version = ?
                """,
                (tenant_id, skill_id, version),
            ).fetchone()
        if row is None:
            return None
        return self._entry_from_row(row)

    def _get_installation_optional(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillInstallation | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM skill_installations
                WHERE tenant_id = ? AND workspace_id = ? AND skill_id = ?
                """,
                (tenant_id, workspace_id, skill_id),
            ).fetchone()
        if row is None:
            return None
        return self._installation_from_row(row)

    def _update_installation_status(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        status: SkillInstallationStatus,
    ) -> SkillInstallation:
        installation = self._get_installation_optional(tenant_id, workspace_id, skill_id)
        if installation is None:
            raise NotFoundError(f"Skill installation not found: {skill_id}")
        updated = installation.model_copy(update={"status": status, "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE skill_installations
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND workspace_id = ? AND skill_id = ?
                """,
                (
                    updated.status.value,
                    self._dt(updated.updated_at),
                    tenant_id,
                    workspace_id,
                    skill_id,
                ),
            )
        return updated

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _ensure_workspace(self, connection, tenant_id: str, workspace_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at) VALUES (?, ?, ?, ?)",
            (workspace_id, tenant_id, workspace_id, self._dt(utc_now())),
        )

    def _entry_from_row(self, row) -> SkillRegistryEntry:
        return SkillRegistryEntry(
            tenant_id=row["tenant_id"],
            manifest=SkillManifest.model_validate(self._loads(row["manifest"])),
            status=SkillStatus(row["status"]),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _installation_from_row(self, row) -> SkillInstallation:
        return SkillInstallation(
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            skill_id=row["skill_id"],
            status=SkillInstallationStatus(row["status"]),
            installed_by_user_id=row["installed_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _json(self, value: dict) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _loads(self, value):
        if not isinstance(value, str):
            return value
        return json.loads(value)

    def _parse_dt(self, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)
