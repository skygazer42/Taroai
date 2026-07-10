import json
from datetime import datetime

from pydantic import BaseModel

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.sso.models import SsoProvider, SsoProviderCreate, SsoProviderEntry, SsoProviderStatus
from taroai.store import NotFoundError


class SqlSsoProviderRegistry(BaseModel):
    config: DatabaseConfig

    def create_or_update(
        self,
        tenant_id: str,
        created_by_user_id: str,
        request: SsoProviderCreate,
    ) -> SsoProviderEntry:
        existing = self._get_optional(tenant_id, request.id)
        now = utc_now()
        entry = SsoProviderEntry(
            tenant_id=tenant_id,
            provider=SsoProvider.model_validate(request.model_dump(mode="json")),
            status=existing.status if existing is not None else SsoProviderStatus.DRAFT,
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
                INSERT INTO sso_provider_configs (
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

    def get_for_tenant(self, tenant_id: str, provider_id: str) -> SsoProviderEntry:
        entry = self._get_optional(tenant_id, provider_id)
        if entry is None:
            raise NotFoundError(f"SSO provider not found: {provider_id}")
        return entry

    def list_for_tenant(self, tenant_id: str) -> list[SsoProviderEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sso_provider_configs
                WHERE tenant_id = ?
                ORDER BY updated_at, provider_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def enable(self, tenant_id: str, provider_id: str) -> SsoProviderEntry:
        return self._update_status(tenant_id, provider_id, SsoProviderStatus.ENABLED)

    def disable(self, tenant_id: str, provider_id: str) -> SsoProviderEntry:
        return self._update_status(tenant_id, provider_id, SsoProviderStatus.DISABLED)

    def find_enabled_for_email(self, tenant_id: str, email: str) -> SsoProviderEntry | None:
        domain = self._email_domain(email)
        if domain is None:
            return None
        for entry in self.list_for_tenant(tenant_id):
            if entry.status == SsoProviderStatus.ENABLED and domain in entry.provider.domains:
                return entry
        return None

    def _update_status(
        self,
        tenant_id: str,
        provider_id: str,
        status: SsoProviderStatus,
    ) -> SsoProviderEntry:
        entry = self.get_for_tenant(tenant_id, provider_id)
        updated = entry.model_copy(update={"status": status, "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sso_provider_configs
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

    def _get_optional(self, tenant_id: str, provider_id: str) -> SsoProviderEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM sso_provider_configs
                WHERE tenant_id = ? AND provider_id = ?
                """,
                (tenant_id, provider_id),
            ).fetchone()
        if row is None:
            return None
        return self._entry_from_row(row)

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _entry_from_row(self, row) -> SsoProviderEntry:
        return SsoProviderEntry(
            tenant_id=row["tenant_id"],
            provider=SsoProvider.model_validate(json.loads(row["config"])),
            status=SsoProviderStatus(row["status"]),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _email_domain(self, email: str) -> str | None:
        if "@" not in email:
            return None
        domain = email.rsplit("@", 1)[1].strip().lower()
        return domain or None

    def _json(self, value: dict) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)
