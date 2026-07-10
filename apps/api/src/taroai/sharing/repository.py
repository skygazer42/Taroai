from datetime import datetime

from pydantic import BaseModel, Field

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import new_id, utc_now
from taroai.sharing.models import (
    ShareGrant,
    ShareGrantCreate,
    ShareGrantStatus,
    SharePermission,
    ShareResourceType,
    ShareSubjectType,
)
from taroai.store import NotFoundError


class ShareGrantStore(BaseModel):
    def create_grant(self, request: ShareGrantCreate) -> ShareGrant:
        raise NotImplementedError

    def list_grants(
        self,
        tenant_id: str,
        resource_type: ShareResourceType | str | None = None,
        resource_id: str | None = None,
    ) -> list[ShareGrant]:
        raise NotImplementedError

    def get_grant(self, tenant_id: str, grant_id: str) -> ShareGrant:
        raise NotImplementedError

    def revoke_grant(
        self,
        tenant_id: str,
        grant_id: str,
        revoked_by_user_id: str,
        now: datetime | None = None,
    ) -> ShareGrant:
        raise NotImplementedError

    def authorize(
        self,
        tenant_id: str,
        resource_type: ShareResourceType | str,
        resource_id: str,
        permission: SharePermission | str,
        user_id: str,
        workspace_id: str | None = None,
        group_ids: list[str] | None = None,
        external_link_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        raise NotImplementedError


class InMemoryShareGrantStore(ShareGrantStore):
    grants: dict[str, ShareGrant] = Field(default_factory=dict)

    def create_grant(self, request: ShareGrantCreate) -> ShareGrant:
        grant = ShareGrant(
            id=new_id("share"),
            status=ShareGrantStatus.ACTIVE,
            created_at=utc_now(),
            **request.model_dump(),
        )
        self.grants[grant.id] = grant
        return grant

    def list_grants(
        self,
        tenant_id: str,
        resource_type: ShareResourceType | str | None = None,
        resource_id: str | None = None,
    ) -> list[ShareGrant]:
        resolved_resource_type = (
            self._resource_type(resource_type) if resource_type is not None else None
        )
        return sorted(
            [
                grant
                for grant in self.grants.values()
                if grant.tenant_id == tenant_id
                and (
                    resolved_resource_type is None
                    or grant.resource_type == resolved_resource_type
                )
                and (resource_id is None or grant.resource_id == resource_id)
            ],
            key=lambda grant: (grant.created_at, grant.id),
        )

    def get_grant(self, tenant_id: str, grant_id: str) -> ShareGrant:
        grant = self.grants.get(grant_id)
        if grant is None or grant.tenant_id != tenant_id:
            raise NotFoundError(f"Share grant not found: {grant_id}")
        return grant

    def revoke_grant(
        self,
        tenant_id: str,
        grant_id: str,
        revoked_by_user_id: str,
        now: datetime | None = None,
    ) -> ShareGrant:
        grant = self.get_grant(tenant_id, grant_id)
        revoked = grant.model_copy(
            update={
                "status": ShareGrantStatus.REVOKED,
                "revoked_by_user_id": revoked_by_user_id,
                "revoked_at": now or utc_now(),
            }
        )
        self.grants[grant_id] = revoked
        return revoked

    def authorize(
        self,
        tenant_id: str,
        resource_type: ShareResourceType | str,
        resource_id: str,
        permission: SharePermission | str,
        user_id: str,
        workspace_id: str | None = None,
        group_ids: list[str] | None = None,
        external_link_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        return any(
            grant_authorizes(
                grant=grant,
                tenant_id=tenant_id,
                resource_type=self._resource_type(resource_type),
                resource_id=resource_id,
                permission=self._permission(permission),
                user_id=user_id,
                workspace_id=workspace_id,
                group_ids=group_ids,
                external_link_id=external_link_id,
                now=now,
            )
            for grant in self.list_grants(tenant_id)
        )

    def _resource_type(self, value: ShareResourceType | str) -> ShareResourceType:
        return (
            value if isinstance(value, ShareResourceType) else ShareResourceType(value)
        )

    def _permission(self, value: SharePermission | str) -> SharePermission:
        return value if isinstance(value, SharePermission) else SharePermission(value)


class SqlShareGrantStore(ShareGrantStore):
    config: DatabaseConfig

    def create_grant(self, request: ShareGrantCreate) -> ShareGrant:
        grant = ShareGrant(
            id=new_id("share"),
            status=ShareGrantStatus.ACTIVE,
            created_at=utc_now(),
            **request.model_dump(),
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, grant.tenant_id)
            connection.execute(
                """
                INSERT INTO share_grants (
                    id, tenant_id, resource_type, resource_id, subject_type,
                    subject_id, permission, status, reason, expires_at,
                    created_by_user_id, created_at, revoked_by_user_id, revoked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.id,
                    grant.tenant_id,
                    grant.resource_type.value,
                    grant.resource_id,
                    grant.subject_type.value,
                    grant.subject_id,
                    grant.permission.value,
                    grant.status.value,
                    grant.reason,
                    self._dt_optional(grant.expires_at),
                    grant.created_by_user_id,
                    self._dt(grant.created_at),
                    grant.revoked_by_user_id,
                    self._dt_optional(grant.revoked_at),
                ),
            )
        return grant

    def list_grants(
        self,
        tenant_id: str,
        resource_type: ShareResourceType | str | None = None,
        resource_id: str | None = None,
    ) -> list[ShareGrant]:
        resolved_resource_type = (
            self._resource_type(resource_type).value
            if resource_type is not None
            else None
        )
        sql = """
            SELECT * FROM share_grants
            WHERE tenant_id = ?
        """
        params: list[str] = [tenant_id]
        if resolved_resource_type is not None:
            sql += " AND resource_type = ?"
            params.append(resolved_resource_type)
        if resource_id is not None:
            sql += " AND resource_id = ?"
            params.append(resource_id)
        sql += " ORDER BY created_at, id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._grant_from_row(row) for row in rows]

    def get_grant(self, tenant_id: str, grant_id: str) -> ShareGrant:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM share_grants
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, grant_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Share grant not found: {grant_id}")
        return self._grant_from_row(row)

    def revoke_grant(
        self,
        tenant_id: str,
        grant_id: str,
        revoked_by_user_id: str,
        now: datetime | None = None,
    ) -> ShareGrant:
        revoked_at = now or utc_now()
        self.get_grant(tenant_id, grant_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE share_grants
                SET status = ?, revoked_by_user_id = ?, revoked_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    ShareGrantStatus.REVOKED.value,
                    revoked_by_user_id,
                    self._dt(revoked_at),
                    tenant_id,
                    grant_id,
                ),
            )
        return self.get_grant(tenant_id, grant_id)

    def authorize(
        self,
        tenant_id: str,
        resource_type: ShareResourceType | str,
        resource_id: str,
        permission: SharePermission | str,
        user_id: str,
        workspace_id: str | None = None,
        group_ids: list[str] | None = None,
        external_link_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        return any(
            grant_authorizes(
                grant=grant,
                tenant_id=tenant_id,
                resource_type=self._resource_type(resource_type),
                resource_id=resource_id,
                permission=self._permission(permission),
                user_id=user_id,
                workspace_id=workspace_id,
                group_ids=group_ids,
                external_link_id=external_link_id,
                now=now,
            )
            for grant in self.list_grants(
                tenant_id=tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )

    def _grant_from_row(self, row) -> ShareGrant:
        return ShareGrant(
            id=row["id"],
            tenant_id=row["tenant_id"],
            resource_type=ShareResourceType(row["resource_type"]),
            resource_id=row["resource_id"],
            subject_type=ShareSubjectType(row["subject_type"]),
            subject_id=row["subject_id"],
            permission=SharePermission(row["permission"]),
            status=ShareGrantStatus(row["status"]),
            reason=row["reason"],
            expires_at=self._parse_dt_optional(row["expires_at"]),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            revoked_by_user_id=row["revoked_by_user_id"],
            revoked_at=self._parse_dt_optional(row["revoked_at"]),
        )

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _resource_type(self, value: ShareResourceType | str) -> ShareResourceType:
        return (
            value if isinstance(value, ShareResourceType) else ShareResourceType(value)
        )

    def _permission(self, value: SharePermission | str) -> SharePermission:
        return value if isinstance(value, SharePermission) else SharePermission(value)

    def _dt_optional(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return self._dt(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt_optional(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


def grant_authorizes(
    grant: ShareGrant,
    tenant_id: str,
    resource_type: ShareResourceType,
    resource_id: str,
    permission: SharePermission,
    user_id: str,
    workspace_id: str | None = None,
    group_ids: list[str] | None = None,
    external_link_id: str | None = None,
    now: datetime | None = None,
) -> bool:
    if grant.tenant_id != tenant_id:
        return False
    if grant.resource_type != resource_type or grant.resource_id != resource_id:
        return False
    if not grant.is_active(now):
        return False
    if not grant.grants_permission(permission):
        return False
    return grant.matches_subject(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=workspace_id,
        group_ids=group_ids,
        external_link_id=external_link_id,
    )
