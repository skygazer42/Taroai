from typing import Any

from pydantic import BaseModel, Field

from taroai.audit import AuditEventCreate
from taroai.identity.models import (
    PasswordHasher,
    Role,
    RoleAssignment,
    UserAccount,
    UserAccountCreate,
    UserAccountStatus,
    normalize_email,
)
from taroai.store import NotFoundError, TenantAccessError


class InMemoryIdentityService(BaseModel):
    password_hasher: PasswordHasher = Field(default_factory=PasswordHasher)
    audit_service: Any | None = None
    users: dict[str, UserAccount] = Field(default_factory=dict)
    user_ids_by_tenant_email: dict[str, str] = Field(default_factory=dict)
    roles: dict[str, Role] = Field(default_factory=dict)
    assignments: list[RoleAssignment] = Field(default_factory=list)

    def create_user(self, request: UserAccountCreate) -> UserAccount:
        lookup_key = self._tenant_email_key(request.tenant_id, request.email)
        if lookup_key in self.user_ids_by_tenant_email:
            raise ValueError(f"User already exists: {request.email}")
        account = UserAccount(
            tenant_id=request.tenant_id,
            email=request.email,
            display_name=request.display_name,
            password_hash=self.password_hasher.hash_password(request.password),
        )
        self.users[account.id] = account
        self.user_ids_by_tenant_email[lookup_key] = account.id
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
        self.users[user_id] = updated
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
        user_id = self.user_ids_by_tenant_email.get(self._tenant_email_key(tenant_id, email))
        if user_id is None:
            raise NotFoundError(f"User not found: {email}")
        return self.get_user(tenant_id, user_id)

    def get_user(self, tenant_id: str, user_id: str) -> UserAccount:
        account = self.users.get(user_id)
        if account is None:
            raise NotFoundError(f"User not found: {user_id}")
        if account.tenant_id != tenant_id:
            raise TenantAccessError(f"User {user_id} is not in tenant {tenant_id}")
        return account

    def create_role(self, role: Role) -> Role:
        self.roles[self._tenant_role_key(role.tenant_id, role.id)] = role
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
        self.assignments.append(assignment)
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
        role = self.roles.get(self._tenant_role_key(tenant_id, role_id))
        if role is None:
            raise NotFoundError(f"Role not found: {role_id}")
        return role

    def has_permission(self, tenant_id: str, user_id: str, action: str, resource: str) -> bool:
        account = self.get_user(tenant_id, user_id)
        if account.status != "active":
            return False
        assigned_role_ids = self.list_role_ids_for_user(tenant_id, user_id)
        roles = [self.get_role(tenant_id, role_id) for role_id in assigned_role_ids]
        return any(
            permission.matches(action, resource)
            for role in roles
            for permission in role.permissions
        )

    def list_role_ids_for_user(self, tenant_id: str, user_id: str) -> list[str]:
        self.get_user(tenant_id, user_id)
        return [
            assignment.role_id
            for assignment in self.assignments
            if assignment.tenant_id == tenant_id and assignment.user_id == user_id
        ]

    def _tenant_email_key(self, tenant_id: str, email: str) -> str:
        return f"{tenant_id}:{normalize_email(email)}"

    def _tenant_role_key(self, tenant_id: str, role_id: str) -> str:
        return f"{tenant_id}:{role_id}"

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
