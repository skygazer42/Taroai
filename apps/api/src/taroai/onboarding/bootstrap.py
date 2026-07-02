from typing import Any

from pydantic import BaseModel

from taroai.audit import AuditEventCreate, AuditService
from taroai.config import Settings
from taroai.db import SqlControlPlaneRepository
from taroai.identity import (
    InMemoryIdentityService,
    Permission,
    Role,
    SqlIdentityService,
    UserAccountCreate,
)
from taroai.onboarding.models import TenantBootstrapRequest, TenantBootstrapResult
from taroai.onboarding.readiness import TenantReadinessService
from taroai.store import InMemoryControlPlaneStore, TenantAccessError


TENANT_OWNER_ROLE_ID = "tenant_owner"
TENANT_OWNER_ROLE_NAME = "Tenant Owner"


class TenantBootstrapService(BaseModel):
    identity_service: InMemoryIdentityService | SqlIdentityService
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository
    settings: Settings
    readiness_service: TenantReadinessService
    audit_service: Any | None = None

    def bootstrap(
        self,
        request: TenantBootstrapRequest,
        bootstrap_token: str | None,
    ) -> TenantBootstrapResult:
        self._require_bootstrap_token(bootstrap_token)
        owner_role = self.identity_service.create_role(
            Role(
                tenant_id=request.tenant_id,
                id=TENANT_OWNER_ROLE_ID,
                name=TENANT_OWNER_ROLE_NAME,
                permissions=self._owner_permissions(request.tenant_id),
            )
        )
        owner = self.identity_service.create_user(
            UserAccountCreate(
                tenant_id=request.tenant_id,
                email=request.owner_email,
                display_name=request.owner_display_name,
                password=request.owner_password,
            )
        )
        self.identity_service.assign_role(request.tenant_id, owner.id, owner_role.id)
        self._record_audit_event(
            tenant_id=request.tenant_id,
            workspace_id=None,
            user_id=owner.id,
            run_id=None,
            event_type="tenant.bootstrap.completed",
            metadata={
                "owner_user_id": owner.id,
                "owner_role_id": owner_role.id,
                "permissions_count": len(owner_role.permissions),
            },
        )
        readiness = self.readiness_service.check_tenant_readiness(request.tenant_id, owner.id)
        return TenantBootstrapResult(
            tenant_id=request.tenant_id,
            owner_user_id=owner.id,
            owner_role_id=owner_role.id,
            readiness=readiness,
        )

    def _require_bootstrap_token(self, bootstrap_token: str | None) -> None:
        if self.settings.tenant_bootstrap_token == "":
            raise TenantAccessError("tenant bootstrap token is not configured")
        if bootstrap_token != self.settings.tenant_bootstrap_token:
            raise TenantAccessError("tenant bootstrap token is invalid")

    def _owner_permissions(self, tenant_id: str) -> list[Permission]:
        resource = f"tenant:{tenant_id}"
        return [
            Permission(action=action, resource=resource)
            for action in [
                "skills.publish",
                "skills.read",
                "skills.install",
                "storage.write",
                "storage.read",
                "memory.write",
                "memory.read",
                "memory.review",
                "knowledge.write",
                "knowledge.read",
                "sandbox.create",
                "sandbox.execute",
                "browser.act",
                "audit.read",
                "billing.read",
                "lifecycle.read",
                "lifecycle.manage",
            ]
        ]

    def _record_audit_event(
        self,
        tenant_id: str,
        workspace_id: str | None,
        user_id: str | None,
        run_id: str | None,
        event_type: str,
        metadata: dict,
    ) -> None:
        service = self.audit_service or AuditService(store=self.store)
        service.record(
            AuditEventCreate(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                event_type=event_type,
                metadata=metadata,
            )
        )
