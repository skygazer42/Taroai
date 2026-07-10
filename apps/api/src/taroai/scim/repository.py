import json
from datetime import datetime

from pydantic import BaseModel

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.scim.models import (
    ScimGroupRoleMapping,
    ScimGroupRoleMappingEntry,
    ScimImportRecord,
    ScimImportResult,
    ScimProvider,
    ScimProviderCreate,
    ScimProviderEntry,
    ScimProviderStatus,
    ScimUserLink,
)
from taroai.store import NotFoundError


class SqlScimProvisioningStore(BaseModel):
    config: DatabaseConfig

    def create_or_update_provider(
        self,
        tenant_id: str,
        created_by_user_id: str,
        request: ScimProviderCreate,
    ) -> ScimProviderEntry:
        existing = self._get_provider_optional(tenant_id, request.id)
        now = utc_now()
        entry = ScimProviderEntry(
            tenant_id=tenant_id,
            provider=ScimProvider.model_validate(request.model_dump(mode="json")),
            status=existing.status if existing is not None else ScimProviderStatus.DRAFT,
            created_by_user_id=(
                existing.created_by_user_id if existing is not None else created_by_user_id
            ),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, tenant_id)
            connection.execute(
                """
                INSERT INTO scim_provider_configs (
                    tenant_id, provider_id, config, status,
                    created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, provider_id) DO UPDATE SET
                    config = excluded.config,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    entry.tenant_id,
                    entry.provider.id,
                    self._json(entry.provider.model_dump(mode="json")),
                    entry.status.value,
                    entry.created_by_user_id,
                    self._dt(entry.created_at),
                    self._dt(entry.updated_at),
                ),
            )
        return entry

    def get_provider(self, tenant_id: str, provider_id: str) -> ScimProviderEntry:
        entry = self._get_provider_optional(tenant_id, provider_id)
        if entry is None:
            raise NotFoundError(f"SCIM provider not found: {provider_id}")
        return entry

    def list_providers(self, tenant_id: str) -> list[ScimProviderEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scim_provider_configs
                WHERE tenant_id = ?
                ORDER BY updated_at, provider_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._provider_from_row(row) for row in rows]

    def enable_provider(self, tenant_id: str, provider_id: str) -> ScimProviderEntry:
        return self._update_provider_status(tenant_id, provider_id, ScimProviderStatus.ENABLED)

    def disable_provider(self, tenant_id: str, provider_id: str) -> ScimProviderEntry:
        return self._update_provider_status(tenant_id, provider_id, ScimProviderStatus.DISABLED)

    def upsert_group_role_mapping(
        self,
        tenant_id: str,
        provider_id: str,
        created_by_user_id: str,
        mapping: ScimGroupRoleMapping,
    ) -> ScimGroupRoleMappingEntry:
        self.get_provider(tenant_id, provider_id)
        existing = self._get_mapping_optional(tenant_id, provider_id, mapping.group_external_id)
        now = utc_now()
        entry = ScimGroupRoleMappingEntry(
            tenant_id=tenant_id,
            provider_id=provider_id,
            mapping=mapping,
            created_by_user_id=(
                existing.created_by_user_id if existing is not None else created_by_user_id
            ),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scim_group_role_mappings (
                    tenant_id, provider_id, group_external_id, role_ids,
                    created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, provider_id, group_external_id) DO UPDATE SET
                    role_ids = excluded.role_ids,
                    updated_at = excluded.updated_at
                """,
                (
                    tenant_id,
                    provider_id,
                    mapping.group_external_id,
                    self._json({"items": mapping.role_ids}),
                    entry.created_by_user_id,
                    self._dt(entry.created_at),
                    self._dt(entry.updated_at),
                ),
            )
        return entry

    def list_group_role_mappings(
        self,
        tenant_id: str,
        provider_id: str,
    ) -> list[ScimGroupRoleMappingEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scim_group_role_mappings
                WHERE tenant_id = ? AND provider_id = ?
                ORDER BY updated_at, group_external_id
                """,
                (tenant_id, provider_id),
            ).fetchall()
        return [self._mapping_from_row(row) for row in rows]

    def upsert_user_link(
        self,
        tenant_id: str,
        provider_id: str,
        external_id: str,
        user_id: str,
        email: str,
        active: bool,
    ) -> ScimUserLink:
        existing = self.find_user_link(tenant_id, provider_id, external_id)
        now = utc_now()
        link = ScimUserLink(
            tenant_id=tenant_id,
            provider_id=provider_id,
            external_id=external_id,
            user_id=user_id,
            email=email,
            active=active,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scim_user_links (
                    tenant_id, provider_id, external_id, user_id, email,
                    active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, provider_id, external_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    email = excluded.email,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    tenant_id,
                    provider_id,
                    external_id,
                    user_id,
                    email,
                    active,
                    self._dt(link.created_at),
                    self._dt(link.updated_at),
                ),
            )
        return link

    def get_user_link(
        self,
        tenant_id: str,
        provider_id: str,
        external_id: str,
    ) -> ScimUserLink:
        link = self.find_user_link(tenant_id, provider_id, external_id)
        if link is None:
            raise NotFoundError(f"SCIM user link not found: {external_id}")
        return link

    def find_user_link(
        self,
        tenant_id: str,
        provider_id: str,
        external_id: str,
    ) -> ScimUserLink | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM scim_user_links
                WHERE tenant_id = ? AND provider_id = ? AND external_id = ?
                """,
                (tenant_id, provider_id, external_id),
            ).fetchone()
        if row is None:
            return None
        return self._user_link_from_row(row)

    def record_import_result(
        self,
        tenant_id: str,
        provider_id: str,
        imported_by_user_id: str,
        result: ScimImportResult,
    ) -> ScimImportRecord:
        record = ScimImportRecord(
            **result.model_dump(mode="json"),
            tenant_id=tenant_id,
            imported_by_user_id=imported_by_user_id,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scim_import_records (
                    tenant_id, provider_id, import_id, users_seen, users_created,
                    users_linked, users_disabled, roles_assigned,
                    imported_by_user_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    provider_id,
                    record.import_id,
                    record.users_seen,
                    record.users_created,
                    record.users_linked,
                    record.users_disabled,
                    record.roles_assigned,
                    imported_by_user_id,
                    self._dt(record.created_at),
                ),
            )
        return record

    def list_import_records(self, tenant_id: str, provider_id: str) -> list[ScimImportRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scim_import_records
                WHERE tenant_id = ? AND provider_id = ?
                ORDER BY created_at, import_id
                """,
                (tenant_id, provider_id),
            ).fetchall()
        return [self._import_record_from_row(row) for row in rows]

    def _update_provider_status(
        self,
        tenant_id: str,
        provider_id: str,
        status: ScimProviderStatus,
    ) -> ScimProviderEntry:
        entry = self.get_provider(tenant_id, provider_id)
        updated = entry.model_copy(update={"status": status, "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scim_provider_configs
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND provider_id = ?
                """,
                (
                    updated.status.value,
                    self._dt(updated.updated_at),
                    tenant_id,
                    provider_id,
                ),
            )
        return updated

    def _get_provider_optional(self, tenant_id: str, provider_id: str) -> ScimProviderEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM scim_provider_configs
                WHERE tenant_id = ? AND provider_id = ?
                """,
                (tenant_id, provider_id),
            ).fetchone()
        if row is None:
            return None
        return self._provider_from_row(row)

    def _get_mapping_optional(
        self,
        tenant_id: str,
        provider_id: str,
        group_external_id: str,
    ) -> ScimGroupRoleMappingEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM scim_group_role_mappings
                WHERE tenant_id = ? AND provider_id = ? AND group_external_id = ?
                """,
                (tenant_id, provider_id, group_external_id),
            ).fetchone()
        if row is None:
            return None
        return self._mapping_from_row(row)

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _provider_from_row(self, row) -> ScimProviderEntry:
        return ScimProviderEntry(
            tenant_id=row["tenant_id"],
            provider=ScimProvider.model_validate(json.loads(row["config"])),
            status=ScimProviderStatus(row["status"]),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _mapping_from_row(self, row) -> ScimGroupRoleMappingEntry:
        return ScimGroupRoleMappingEntry(
            tenant_id=row["tenant_id"],
            provider_id=row["provider_id"],
            mapping=ScimGroupRoleMapping(
                group_external_id=row["group_external_id"],
                role_ids=json.loads(row["role_ids"])["items"],
            ),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _user_link_from_row(self, row) -> ScimUserLink:
        return ScimUserLink(
            tenant_id=row["tenant_id"],
            provider_id=row["provider_id"],
            external_id=row["external_id"],
            user_id=row["user_id"],
            email=row["email"],
            active=bool(row["active"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _import_record_from_row(self, row) -> ScimImportRecord:
        return ScimImportRecord(
            tenant_id=row["tenant_id"],
            provider_id=row["provider_id"],
            import_id=row["import_id"],
            users_seen=row["users_seen"],
            users_created=row["users_created"],
            users_linked=row["users_linked"],
            users_disabled=row["users_disabled"],
            roles_assigned=row["roles_assigned"],
            imported_by_user_id=row["imported_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _json(self, value: dict) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)
