import json
import sqlite3
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from taroai.audit import AuditEventCreate
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.identity.models import (
    PasswordHasher,
    Permission,
    Role,
    RoleAssignment,
    UserAccount,
    UserAccountCreate,
    UserAccountStatus,
    normalize_email,
)
from taroai.store import NotFoundError, TenantAccessError


class SqlIdentityService(BaseModel):
    config: DatabaseConfig
    password_hasher: PasswordHasher = Field(default_factory=PasswordHasher)
    audit_service: Any | None = None

    def create_user(self, request: UserAccountCreate) -> UserAccount:
        account = UserAccount(
            tenant_id=request.tenant_id,
            email=request.email,
            display_name=request.display_name,
            password_hash=self.password_hasher.hash_password(request.password),
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, request.tenant_id)
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        id, tenant_id, email, display_name, password_hash, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account.id,
                        account.tenant_id,
                        account.email,
                        account.display_name,
                        account.password_hash,
                        account.status,
                        self._dt(account.created_at),
                    ),
                )
            except Exception as error:
                if not (
                    isinstance(error, sqlite3.IntegrityError)
                    or getattr(error, "sqlstate", "") == "23505"
                ):
                    raise
                raise ValueError(f"User already exists: {request.email}") from error
        self._record_audit_event(
            tenant_id=account.tenant_id,
            user_id=account.id,
            event_type="identity.user.created",
            metadata={
                "user_id": account.id,
                "email": account.email,
                "display_name": account.display_name,
                "status": account.status,
            },
        )
        return account

    def verify_password(self, tenant_id: str, email: str, password: str) -> bool:
        account = self.get_user_by_email(tenant_id, email)
        return self.password_hasher.verify_password(password, account.password_hash)

    def disable_user(self, tenant_id: str, user_id: str) -> UserAccount:
        return self._set_user_status(
            tenant_id=tenant_id,
            user_id=user_id,
            status="disabled",
            event_type="identity.user.disabled",
        )

    def mark_user_pending(self, tenant_id: str, user_id: str) -> UserAccount:
        return self._set_user_status(
            tenant_id=tenant_id,
            user_id=user_id,
            status="pending",
            event_type="identity.user.pending",
        )

    def activate_user(self, tenant_id: str, user_id: str) -> UserAccount:
        return self._set_user_status(
            tenant_id=tenant_id,
            user_id=user_id,
            status="active",
            event_type="identity.user.activated",
        )

    def delete_user(self, tenant_id: str, user_id: str) -> UserAccount:
        return self._set_user_status(
            tenant_id=tenant_id,
            user_id=user_id,
            status="deleted",
            event_type="identity.user.deleted",
        )

    def _set_user_status(
        self,
        tenant_id: str,
        user_id: str,
        status: UserAccountStatus,
        event_type: str,
    ) -> UserAccount:
        account = self.get_user(tenant_id, user_id)
        updated = account.model_copy(update={"status": status})
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET status = ? WHERE tenant_id = ? AND id = ?",
                (updated.status, tenant_id, user_id),
            )
        self._record_audit_event(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            metadata={
                "user_id": user_id,
                "status": updated.status,
            },
        )
        return updated

    def get_user_by_email(self, tenant_id: str, email: str) -> UserAccount:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE tenant_id = ? AND lower(trim(email)) = lower(trim(?))",
                (tenant_id, normalize_email(email)),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"User not found: {email}")
        return self._user_from_row(row)

    def find_users_by_email(self, email: str) -> list[UserAccount]:
        with self._connect() as connection:
            tenant_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM tenants ORDER BY id"
                ).fetchall()
            ]
            rows = []
            for tenant_id in tenant_ids:
                row = connection.execute(
                    "SELECT * FROM users WHERE tenant_id = ? AND lower(trim(email)) = lower(trim(?))",
                    (tenant_id, normalize_email(email)),
                ).fetchone()
                if row is not None:
                    rows.append(row)
        return [self._user_from_row(row) for row in rows]

    def list_users(self, tenant_id: str) -> list[UserAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM users
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._user_from_row(row) for row in rows]

    def get_user(self, tenant_id: str, user_id: str) -> UserAccount:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE tenant_id = ? AND id = ?",
                (tenant_id, user_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"User not found: {user_id}")
        if row["tenant_id"] != tenant_id:
            raise TenantAccessError(f"User {user_id} is not in tenant {tenant_id}")
        return self._user_from_row(row)

    def create_role(self, role: Role) -> Role:
        with self._connect() as connection:
            self._ensure_tenant(connection, role.tenant_id)
            connection.execute(
                """
                INSERT INTO roles (id, tenant_id, name, permissions, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    name = excluded.name,
                    permissions = excluded.permissions
                """,
                (
                    role.id,
                    role.tenant_id,
                    role.name,
                    self._json([permission.model_dump(mode="json") for permission in role.permissions]),
                    self._dt(utc_now()),
                ),
            )
        self._record_audit_event(
            tenant_id=role.tenant_id,
            user_id=None,
            event_type="identity.role.created",
            metadata={
                "role_id": role.id,
                "role_name": role.name,
                "permissions_count": len(role.permissions),
            },
        )
        return role

    def assign_role(self, tenant_id: str, user_id: str, role_id: str) -> RoleAssignment:
        self.get_user(tenant_id, user_id)
        self.get_role(tenant_id, role_id)
        assignment = RoleAssignment(tenant_id=tenant_id, user_id=user_id, role_id=role_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO role_assignments (
                    tenant_id, user_id, role_id, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    assignment.tenant_id,
                    assignment.user_id,
                    assignment.role_id,
                    self._dt(assignment.created_at),
                ),
            )
        self._record_audit_event(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="identity.role.assigned",
            metadata={
                "assigned_user_id": user_id,
                "role_id": role_id,
            },
        )
        return assignment

    def get_role(self, tenant_id: str, role_id: str) -> Role:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM roles WHERE tenant_id = ? AND id = ?",
                (tenant_id, role_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Role not found: {role_id}")
        return self._role_from_row(row)

    def has_permission(self, tenant_id: str, user_id: str, action: str, resource: str) -> bool:
        account = self.get_user(tenant_id, user_id)
        if account.status != "active":
            return False
        roles = [
            self.get_role(tenant_id, role_id)
            for role_id in self.list_role_ids_for_user(tenant_id, user_id)
        ]
        return any(
            permission.matches(action, resource)
            for role in roles
            for permission in role.permissions
        )

    def list_role_ids_for_user(self, tenant_id: str, user_id: str) -> list[str]:
        self.get_user(tenant_id, user_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role_id FROM role_assignments
                WHERE tenant_id = ? AND user_id = ?
                ORDER BY created_at, role_id
                """,
                (tenant_id, user_id),
            ).fetchall()
        return [row["role_id"] for row in rows]

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _user_from_row(self, row) -> UserAccount:
        return UserAccount(
            id=row["id"],
            tenant_id=row["tenant_id"],
            email=row["email"],
            display_name=row["display_name"] or "",
            password_hash=row["password_hash"],
            status=row["status"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _role_from_row(self, row) -> Role:
        return Role(
            tenant_id=row["tenant_id"],
            id=row["id"],
            name=row["name"],
            permissions=[
                Permission.model_validate(permission)
                for permission in self._loads(row["permissions"])
            ],
        )

    def _json(self, value) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: Any | None):
        if value is None:
            return []
        if not isinstance(value, str):
            return value
        return json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    def _record_audit_event(
        self,
        tenant_id: str,
        user_id: str | None,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=tenant_id,
                workspace_id=None,
                user_id=user_id,
                run_id=None,
                event_type=event_type,
                metadata=metadata,
            )
        )
