import json
from datetime import datetime

from pydantic import BaseModel

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.solution_packs.models import (
    SolutionPackEntry,
    SolutionPackInstallation,
    SolutionPackInstallationStatus,
    SolutionPackManifest,
    SolutionPackStatus,
)
from taroai.store import NotFoundError


class SqlSolutionPackRegistry(BaseModel):
    config: DatabaseConfig

    def register_for_tenant(
        self,
        tenant_id: str,
        created_by_user_id: str,
        manifest: SolutionPackManifest,
    ) -> SolutionPackEntry:
        existing = self._get_optional(tenant_id, manifest.id)
        if self._get_version_optional(tenant_id, manifest.id, manifest.version) is not None:
            raise ValueError(f"Solution pack already exists: {manifest.id}@{manifest.version}")
        now = utc_now()
        entry = SolutionPackEntry(
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
                INSERT INTO solution_pack_versions (
                    tenant_id, pack_id, version, manifest, status,
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
                INSERT INTO solution_pack_entries (
                    tenant_id, pack_id, version, manifest, status,
                    created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, pack_id) DO UPDATE SET
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

    def get_for_tenant(self, tenant_id: str, pack_id: str) -> SolutionPackEntry:
        entry = self._get_optional(tenant_id, pack_id)
        if entry is None:
            raise NotFoundError(f"Solution pack not found: {pack_id}")
        return entry

    def list_for_tenant(self, tenant_id: str) -> list[SolutionPackEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM solution_pack_entries
                WHERE tenant_id = ?
                ORDER BY updated_at, pack_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def list_versions(self, tenant_id: str, pack_id: str) -> list[SolutionPackEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM solution_pack_versions
                WHERE tenant_id = ? AND pack_id = ?
                ORDER BY created_at, version
                """,
                (tenant_id, pack_id),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

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
        existing = self._get_installation_optional(tenant_id, pack_id)
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
        with self._connect() as connection:
            self._ensure_tenant(connection, tenant_id)
            connection.execute(
                """
                INSERT INTO solution_pack_installations (
                    tenant_id, pack_id, version, workspace_ids, installed_skill_ids,
                    status, installed_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, pack_id) DO UPDATE SET
                    version = excluded.version,
                    workspace_ids = excluded.workspace_ids,
                    installed_skill_ids = excluded.installed_skill_ids,
                    status = excluded.status,
                    installed_by_user_id = excluded.installed_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    installation.tenant_id,
                    installation.pack_id,
                    installation.version,
                    self._json({"items": installation.workspace_ids}),
                    self._json({"items": installation.installed_skill_ids}),
                    installation.status.value,
                    installation.installed_by_user_id,
                    self._dt(installation.created_at),
                    self._dt(installation.updated_at),
                ),
            )
        return installation

    def get_installation(self, tenant_id: str, pack_id: str) -> SolutionPackInstallation:
        installation = self._get_installation_optional(tenant_id, pack_id)
        if installation is None:
            raise NotFoundError(f"Solution pack installation not found: {pack_id}")
        return installation

    def list_installations(self, tenant_id: str) -> list[SolutionPackInstallation]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM solution_pack_installations
                WHERE tenant_id = ?
                ORDER BY updated_at, pack_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._installation_from_row(row) for row in rows]

    def update_installation_status(
        self,
        tenant_id: str,
        pack_id: str,
        status: SolutionPackInstallationStatus,
    ) -> SolutionPackInstallation:
        installation = self.get_installation(tenant_id, pack_id)
        updated = installation.model_copy(
            update={"status": status, "updated_at": utc_now()}
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE solution_pack_installations
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND pack_id = ?
                """,
                (
                    updated.status.value,
                    self._dt(updated.updated_at),
                    tenant_id,
                    pack_id,
                ),
            )
        return updated

    def _update_status(
        self,
        tenant_id: str,
        pack_id: str,
        status: SolutionPackStatus,
    ) -> SolutionPackEntry:
        entry = self.get_for_tenant(tenant_id, pack_id)
        updated = entry.model_copy(update={"status": status, "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE solution_pack_entries
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND pack_id = ?
                """,
                (
                    updated.status.value,
                    self._dt(updated.updated_at),
                    tenant_id,
                    pack_id,
                ),
            )
        return updated

    def _get_optional(self, tenant_id: str, pack_id: str) -> SolutionPackEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM solution_pack_entries
                WHERE tenant_id = ? AND pack_id = ?
                """,
                (tenant_id, pack_id),
            ).fetchone()
        if row is None:
            return None
        return self._entry_from_row(row)

    def _get_version_optional(
        self,
        tenant_id: str,
        pack_id: str,
        version: str,
    ) -> SolutionPackEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM solution_pack_versions
                WHERE tenant_id = ? AND pack_id = ? AND version = ?
                """,
                (tenant_id, pack_id, version),
            ).fetchone()
        if row is None:
            return None
        return self._entry_from_row(row)

    def _get_installation_optional(
        self,
        tenant_id: str,
        pack_id: str,
    ) -> SolutionPackInstallation | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM solution_pack_installations
                WHERE tenant_id = ? AND pack_id = ?
                """,
                (tenant_id, pack_id),
            ).fetchone()
        if row is None:
            return None
        return self._installation_from_row(row)

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _entry_from_row(self, row) -> SolutionPackEntry:
        return SolutionPackEntry(
            tenant_id=row["tenant_id"],
            manifest=SolutionPackManifest.model_validate(json.loads(row["manifest"])),
            status=SolutionPackStatus(row["status"]),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _installation_from_row(self, row) -> SolutionPackInstallation:
        return SolutionPackInstallation(
            tenant_id=row["tenant_id"],
            pack_id=row["pack_id"],
            version=row["version"],
            workspace_ids=json.loads(row["workspace_ids"])["items"],
            installed_skill_ids=json.loads(row["installed_skill_ids"])["items"],
            status=SolutionPackInstallationStatus(row["status"]),
            installed_by_user_id=row["installed_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _json(self, value: dict) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)
