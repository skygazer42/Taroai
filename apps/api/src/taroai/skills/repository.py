import json
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.skills.evaluation import (
    SkillEvaluationCaseResult,
    SkillEvaluationRun,
    SkillEvaluationStatus,
)
from taroai.skills.manifest import SkillManifest
from taroai.skills.package import (
    SkillDependency,
    SkillFrontmatter,
    SkillPackage,
    SkillPackageFile,
    SkillPackageFileKind,
    SkillPackageKind,
    SkillPackageProvenance,
    SkillPackageSourceType,
)
from taroai.skills.registry import (
    SkillInstallation,
    SkillInstallationStatus,
    SkillMarketplaceAnalytics,
    SkillPackageRecord,
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

    def register_package_for_tenant(
        self,
        tenant_id: str,
        created_by_user_id: str,
        package: SkillPackage,
    ) -> SkillPackageRecord:
        if self._get_package_record_optional(
            tenant_id,
            package.skill_id,
            package.version,
        ) is not None:
            raise ValueError(
                f"Skill package already exists: {package.skill_id}@{package.version}"
            )
        version_entry = self._get_version_optional(
            tenant_id,
            package.skill_id,
            package.version,
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
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO skill_packages (
                    tenant_id, skill_id, version, package_kind, manifest,
                    frontmatter, skill_md, taroai_config, status,
                    package_digest, source_type, source_url, source_ref,
                    source_digest, resolved_dependencies, release_notes,
                    created_by_user_id, created_at, published_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    package.skill_id,
                    package.version,
                    package.package_kind.value,
                    self._json(package.manifest.model_dump(mode="json")),
                    self._json(package.frontmatter.model_dump(mode="json")),
                    package.skill_md,
                    self._json(package.taroai_config),
                    record.status.value,
                    package.package_digest,
                    package.provenance.source_type.value,
                    package.provenance.source_url,
                    package.provenance.source_ref,
                    package.provenance.source_digest,
                    self._json(
                        [
                            dependency.model_dump(mode="json")
                            for dependency in package.resolved_dependencies
                        ]
                    ),
                    package.release_notes,
                    created_by_user_id,
                    self._dt(now),
                    None,
                    self._dt(now),
                ),
            )
            for item in package.files:
                connection.execute(
                    """
                    INSERT INTO skill_package_files (
                        tenant_id, skill_id, version, path, kind,
                        size_bytes, content_digest, content, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        package.skill_id,
                        package.version,
                        item.path,
                        item.kind.value,
                        item.size_bytes,
                        item.content_digest,
                        item.content,
                        self._dt(now),
                    ),
                )
        return record

    def get_package_record(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillPackageRecord:
        record = self._get_package_record_optional(tenant_id, skill_id, version)
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
    ) -> list[SkillPackageRecord]:
        query = "SELECT * FROM skill_packages WHERE tenant_id = ?"
        parameters: tuple = (tenant_id,)
        if skill_id is not None:
            query += " AND skill_id = ?"
            parameters = (tenant_id, skill_id)
        query += " ORDER BY skill_id, created_at, version"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._package_record_from_row(row) for row in rows]

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
        if self._get_package_record_optional(
            tenant_id,
            skill_id,
            current.manifest.version,
        ) is not None:
            self.publish_package(tenant_id, skill_id, current.manifest.version)
            return self.get_for_tenant(tenant_id, skill_id)
        return self._update_status(tenant_id, skill_id, SkillStatus.PUBLISHED)

    def disable(self, tenant_id: str, skill_id: str) -> SkillRegistryEntry:
        current = self.get_for_tenant(tenant_id, skill_id)
        if self._get_package_record_optional(
            tenant_id,
            skill_id,
            current.manifest.version,
        ) is not None:
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
        package_record = self._get_package_record_optional(
            tenant_id,
            skill_id,
            target_version,
        )
        if package_record is not None:
            if package_record.status != SkillStatus.PUBLISHED:
                raise ValueError(f"Skill package is not published: {skill_id}@{target_version}")
            if (
                package_digest is not None
                and package_record.package.package_digest != package_digest
            ):
                raise ValueError("requested package digest does not match published package")
            return self._persist_package_installation(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                installed_by_user_id=installed_by_user_id,
                package_record=package_record,
            )
        if version is not None or package_digest is not None:
            raise NotFoundError(f"Skill package not found: {skill_id}@{target_version}")
        if entry.status != SkillStatus.PUBLISHED:
            raise ValueError(f"Skill is not published: {skill_id}")
        existing = self._get_installation_optional(tenant_id, workspace_id, skill_id)
        now = utc_now()
        installation = SkillInstallation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            installed_by_user_id=installed_by_user_id,
            installed_version=entry.manifest.version,
            package_kind=SkillPackageKind.LEGACY_MANIFEST,
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
                    installed_by_user_id, installed_version, package_digest,
                    source_digest, resolved_dependencies, package_kind,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, workspace_id, skill_id) DO UPDATE SET
                    status = excluded.status,
                    installed_by_user_id = excluded.installed_by_user_id,
                    installed_version = excluded.installed_version,
                    package_digest = excluded.package_digest,
                    source_digest = excluded.source_digest,
                    resolved_dependencies = excluded.resolved_dependencies,
                    package_kind = excluded.package_kind,
                    updated_at = excluded.updated_at
                """,
                (
                    installation.tenant_id,
                    installation.workspace_id,
                    installation.skill_id,
                    installation.status.value,
                    installation.installed_by_user_id,
                    installation.installed_version,
                    installation.package_digest,
                    installation.source_digest,
                    self._json(installation.resolved_dependencies),
                    installation.package_kind.value,
                    self._dt(installation.created_at),
                    self._dt(installation.updated_at),
                ),
            )
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
        return self._persist_package_installation(
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
        installation = self._get_installation_optional(
            tenant_id,
            workspace_id,
            skill_id,
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
        return self._resolve_complete_package_pin(
            self.get_installation(tenant_id, workspace_id, skill_id)
        )

    def list_discoverable_packages(
        self,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        department_id: str | None = None,
    ) -> list[SkillPackage]:
        discovered: list[SkillPackage] = []
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
        package = self.get_package_version(run.tenant_id, run.skill_id, run.version)
        if package.package_digest != run.package_digest:
            raise ValueError("evaluation run package digest does not match immutable package")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO skill_evaluation_runs (
                    id, tenant_id, workspace_id, skill_id, version,
                    package_digest, suite_digest, evaluator_version, status,
                    minimum_score, score, passed, side_effect_violations,
                    total_cost, duration_seconds, case_results,
                    created_by_user_id, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.tenant_id,
                    run.workspace_id,
                    run.skill_id,
                    run.version,
                    run.package_digest,
                    run.suite_digest,
                    run.evaluator_version,
                    run.status.value,
                    run.minimum_score,
                    run.score,
                    run.passed,
                    self._json(run.side_effect_violations),
                    float(run.total_cost),
                    run.duration_seconds,
                    self._json(
                        [result.model_dump(mode="json") for result in run.case_results]
                    ),
                    run.created_by_user_id,
                    self._dt(run.created_at),
                    self._dt(run.completed_at) if run.completed_at is not None else None,
                ),
            )
        return run

    def list_evaluation_runs(
        self,
        tenant_id: str,
        skill_id: str,
        version: str | None = None,
    ) -> list[SkillEvaluationRun]:
        query = """
            SELECT * FROM skill_evaluation_runs
            WHERE tenant_id = ? AND skill_id = ?
        """
        parameters: tuple = (tenant_id, skill_id)
        if version is not None:
            query += " AND version = ?"
            parameters = (tenant_id, skill_id, version)
        query += " ORDER BY created_at, id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._evaluation_run_from_row(row) for row in rows]

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

    def _update_package_status(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
        status: SkillStatus,
    ) -> SkillPackageRecord:
        record = self.get_package_record(tenant_id, skill_id, version)
        now = utc_now()
        published_at = (
            now if status == SkillStatus.PUBLISHED else record.published_at
        )

    def uninstall_for_workspace(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillInstallation:
        installation = self.get_installation(tenant_id, workspace_id, skill_id)
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM skill_installations
                WHERE tenant_id = ? AND workspace_id = ? AND skill_id = ?
                """,
                (tenant_id, workspace_id, skill_id),
            )
        return installation
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE skill_packages
                SET status = ?, published_at = ?, updated_at = ?
                WHERE tenant_id = ? AND skill_id = ? AND version = ?
                """,
                (
                    status.value,
                    self._dt(published_at) if published_at is not None else None,
                    self._dt(now),
                    tenant_id,
                    skill_id,
                    version,
                ),
            )
            connection.execute(
                """
                UPDATE skill_registry_versions
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND skill_id = ? AND version = ?
                """,
                (status.value, self._dt(now), tenant_id, skill_id, version),
            )
            connection.execute(
                """
                UPDATE skill_registry_entries
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND skill_id = ? AND version = ?
                """,
                (status.value, self._dt(now), tenant_id, skill_id, version),
            )
        return self.get_package_record(tenant_id, skill_id, version)

    def _persist_package_installation(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        installed_by_user_id: str,
        package_record: SkillPackageRecord,
    ) -> SkillInstallation:
        package = package_record.package
        existing = self._get_installation_optional(
            tenant_id,
            workspace_id,
            package.skill_id,
        )
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
        with self._connect() as connection:
            self._ensure_tenant(connection, tenant_id)
            self._ensure_workspace(connection, tenant_id, workspace_id)
            connection.execute(
                """
                INSERT INTO skill_installations (
                    tenant_id, workspace_id, skill_id, status,
                    installed_by_user_id, installed_version, package_digest,
                    source_digest, resolved_dependencies, package_kind,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, workspace_id, skill_id) DO UPDATE SET
                    status = excluded.status,
                    installed_by_user_id = excluded.installed_by_user_id,
                    installed_version = excluded.installed_version,
                    package_digest = excluded.package_digest,
                    source_digest = excluded.source_digest,
                    resolved_dependencies = excluded.resolved_dependencies,
                    package_kind = excluded.package_kind,
                    updated_at = excluded.updated_at
                """,
                (
                    installation.tenant_id,
                    installation.workspace_id,
                    installation.skill_id,
                    installation.status.value,
                    installation.installed_by_user_id,
                    installation.installed_version,
                    installation.package_digest,
                    installation.source_digest,
                    self._json(installation.resolved_dependencies),
                    installation.package_kind.value,
                    self._dt(installation.created_at),
                    self._dt(installation.updated_at),
                ),
            )
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

    def _get_package_record_optional(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillPackageRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM skill_packages
                WHERE tenant_id = ? AND skill_id = ? AND version = ?
                """,
                (tenant_id, skill_id, version),
            ).fetchone()
        if row is None:
            return None
        return self._package_record_from_row(row)

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
            installed_version=row["installed_version"],
            package_digest=row["package_digest"],
            source_digest=row["source_digest"],
            resolved_dependencies=self._loads(row["resolved_dependencies"]),
            package_kind=SkillPackageKind(row["package_kind"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _package_record_from_row(self, row) -> SkillPackageRecord:
        tenant_id = row["tenant_id"]
        skill_id = row["skill_id"]
        version = row["version"]
        package = SkillPackage(
            manifest=SkillManifest.model_validate(self._loads(row["manifest"])),
            frontmatter=SkillFrontmatter.model_validate(
                self._loads(row["frontmatter"])
            ),
            skill_md=row["skill_md"],
            taroai_config=self._loads(row["taroai_config"]),
            files=tuple(self._package_files(tenant_id, skill_id, version)),
            package_digest=row["package_digest"],
            provenance=SkillPackageProvenance(
                source_type=SkillPackageSourceType(row["source_type"]),
                source_url=row["source_url"],
                source_ref=row["source_ref"],
                source_digest=row["source_digest"],
            ),
            resolved_dependencies=tuple(
                SkillDependency.model_validate(value)
                for value in self._loads(row["resolved_dependencies"])
            ),
            package_kind=SkillPackageKind(row["package_kind"]),
            release_notes=row["release_notes"],
        )
        return SkillPackageRecord(
            tenant_id=tenant_id,
            package=package,
            status=SkillStatus(row["status"]),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            published_at=(
                self._parse_dt(row["published_at"])
                if row["published_at"] is not None
                else None
            ),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _package_files(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> list[SkillPackageFile]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM skill_package_files
                WHERE tenant_id = ? AND skill_id = ? AND version = ?
                ORDER BY path
                """,
                (tenant_id, skill_id, version),
            ).fetchall()
        return [
            SkillPackageFile(
                path=row["path"],
                kind=SkillPackageFileKind(row["kind"]),
                size_bytes=int(row["size_bytes"]),
                content_digest=row["content_digest"],
                content=bytes(row["content"]),
            )
            for row in rows
        ]

    def _evaluation_run_from_row(self, row) -> SkillEvaluationRun:
        return SkillEvaluationRun(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            skill_id=row["skill_id"],
            version=row["version"],
            package_digest=row["package_digest"],
            suite_digest=row["suite_digest"],
            evaluator_version=row["evaluator_version"],
            status=SkillEvaluationStatus(row["status"]),
            minimum_score=float(row["minimum_score"]),
            score=float(row["score"]) if row["score"] is not None else None,
            passed=(bool(row["passed"]) if row["passed"] is not None else None),
            side_effect_violations=self._loads(row["side_effect_violations"]),
            total_cost=Decimal(str(row["total_cost"])),
            duration_seconds=float(row["duration_seconds"]),
            case_results=tuple(
                SkillEvaluationCaseResult.model_validate(value)
                for value in self._loads(row["case_results"])
            ),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            completed_at=(
                self._parse_dt(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
        )

    def _json(self, value) -> str:
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
