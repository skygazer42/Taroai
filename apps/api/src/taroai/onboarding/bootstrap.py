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
from taroai.knowledge import (
    InMemoryKnowledgeService,
    KnowledgeBaseCreate,
    SqlKnowledgeService,
)
from taroai.onboarding.models import TenantBootstrapRequest, TenantBootstrapResult
from taroai.onboarding.readiness import TenantReadinessService
from taroai.store import InMemoryControlPlaneStore, NotFoundError, TenantAccessError
from taroai.tenancy import ensure_tenant_member_role


TENANT_OWNER_ROLE_ID = "tenant_owner"
TENANT_OWNER_ROLE_NAME = "Tenant Owner"
class TenantBootstrapService(BaseModel):
    identity_service: InMemoryIdentityService | SqlIdentityService
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository
    settings: Settings
    readiness_service: TenantReadinessService
    audit_service: Any | None = None
    knowledge_service: InMemoryKnowledgeService | SqlKnowledgeService | None = None
    solution_pack_service: Any | None = None

    def bootstrap(
        self,
        request: TenantBootstrapRequest,
        bootstrap_token: str | None,
    ) -> TenantBootstrapResult:
        self._require_bootstrap_token(bootstrap_token)
        tenant_id = self._tenant_id(request)
        tenant_slug = self._tenant_slug(request, tenant_id)
        starter_workspace_id = request.starter_workspace_id or self._starter_workspace_id(tenant_slug)
        owner_role = self._get_or_create_owner_role(tenant_id)
        ensure_tenant_member_role(self.identity_service, tenant_id)
        owner = self._get_or_create_owner(
            tenant_id=tenant_id,
            request=request,
        )
        self.store.register_workspace(
            tenant_id,
            starter_workspace_id,
            owner.id,
            request.starter_workspace_name.strip(),
        )
        self._assign_owner_role(
            tenant_id=tenant_id,
            owner_user_id=owner.id,
            owner_role_id=owner_role.id,
        )
        starter_knowledge_base_id = self._seed_starter_knowledge_base(
            tenant_id=tenant_id,
            owner_user_id=owner.id,
            workspace_id=starter_workspace_id,
            name=request.starter_knowledge_base_name,
        )
        starter_skill_ids: list[str] = []
        starter_solution_pack_skill_ids = self._seed_starter_solution_packs(
            tenant_id=tenant_id,
            owner_user_id=owner.id,
            workspace_id=starter_workspace_id,
            pack_ids=request.starter_solution_pack_ids,
        )
        self._record_bootstrap_completed_once(
            tenant_id=tenant_id,
            tenant_slug=tenant_slug,
            owner_user_id=owner.id,
            owner_role=owner_role,
            starter_workspace_id=starter_workspace_id,
            starter_knowledge_base_id=starter_knowledge_base_id,
            starter_skill_ids=starter_skill_ids,
            starter_solution_pack_ids=request.starter_solution_pack_ids,
            starter_solution_pack_skill_ids=starter_solution_pack_skill_ids,
        )
        readiness = self.readiness_service.check_tenant_readiness(tenant_id, owner.id)
        return TenantBootstrapResult(
            tenant_id=tenant_id,
            tenant_slug=tenant_slug,
            owner_user_id=owner.id,
            owner_role_id=owner_role.id,
            starter_workspace_id=starter_workspace_id,
            starter_knowledge_base_id=starter_knowledge_base_id,
            starter_skill_ids=starter_skill_ids,
            starter_solution_pack_ids=request.starter_solution_pack_ids,
            starter_solution_pack_skill_ids=starter_solution_pack_skill_ids,
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
                "skills.invoke",
                "connectors.read",
                "connectors.manage",
                "connectors.invoke",
                "triggers.read",
                "triggers.manage",
                "triggers.invoke",
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
                "model_policy.read",
                "model_policy.manage",
                "model_policy.approve",
                "model_providers.read",
                "model_providers.manage",
                "model_providers.approve",
                "solution_packs.read",
                "solution_packs.manage",
                "solution_packs.install",
                "customer_success.read",
                "customer_success.feedback",
                "customer_success.manage",
                "lifecycle.read",
                "lifecycle.manage",
                "sso.read",
                "sso.manage",
                "scim.read",
                "scim.manage",
                "organization.manage",
            ]
        ]

    def _tenant_id(self, request: TenantBootstrapRequest) -> str:
        if request.tenant_id is not None:
            return request.tenant_id
        return f"tenant_{self._tenant_slug(request, None).replace('-', '_')}"

    def _tenant_slug(self, request: TenantBootstrapRequest, tenant_id: str | None) -> str:
        if request.tenant_slug is not None:
            return request.tenant_slug
        resolved_tenant_id = tenant_id or request.tenant_id or ""
        if resolved_tenant_id.startswith("tenant_"):
            return resolved_tenant_id.removeprefix("tenant_").replace("_", "-")
        return resolved_tenant_id.replace("_", "-")

    def _starter_workspace_id(self, tenant_slug: str) -> str:
        return f"workspace_{tenant_slug.replace('-', '_')}"

    def _get_or_create_owner_role(self, tenant_id: str) -> Role:
        desired_permissions = self._owner_permissions(tenant_id)
        try:
            role = self.identity_service.get_role(tenant_id, TENANT_OWNER_ROLE_ID)
        except NotFoundError:
            return self.identity_service.create_role(
                Role(
                    tenant_id=tenant_id,
                    id=TENANT_OWNER_ROLE_ID,
                    name=TENANT_OWNER_ROLE_NAME,
                    permissions=desired_permissions,
                )
            )
        merged_permissions = self._merge_permissions(
            current_permissions=role.permissions,
            desired_permissions=desired_permissions,
        )
        if len(merged_permissions) == len(role.permissions):
            return role
        return self.identity_service.create_role(
            role.model_copy(update={"permissions": merged_permissions})
        )

    def _merge_permissions(
        self,
        current_permissions: list[Permission],
        desired_permissions: list[Permission],
    ) -> list[Permission]:
        merged = list(current_permissions)
        seen = {
            (permission.action, permission.resource)
            for permission in current_permissions
        }
        for permission in desired_permissions:
            key = (permission.action, permission.resource)
            if key in seen:
                continue
            merged.append(permission)
            seen.add(key)
        return merged

    def _get_or_create_owner(
        self,
        tenant_id: str,
        request: TenantBootstrapRequest,
    ):
        try:
            return self.identity_service.get_user_by_email(tenant_id, request.owner_email)
        except NotFoundError:
            return self.identity_service.create_user(
                UserAccountCreate(
                    tenant_id=tenant_id,
                    email=request.owner_email,
                    display_name=request.owner_display_name,
                    password=request.owner_password,
                )
            )

    def _assign_owner_role(
        self,
        tenant_id: str,
        owner_user_id: str,
        owner_role_id: str,
    ) -> None:
        if owner_role_id in self.identity_service.list_role_ids_for_user(tenant_id, owner_user_id):
            return
        self.identity_service.assign_role(tenant_id, owner_user_id, owner_role_id)

    def _seed_starter_knowledge_base(
        self,
        tenant_id: str,
        owner_user_id: str,
        workspace_id: str,
        name: str,
    ) -> str:
        if self.knowledge_service is None:
            return ""
        for knowledge_base in self.knowledge_service.list_bases_for_workspace(tenant_id, workspace_id):
            if knowledge_base.name == name:
                return knowledge_base.id
        knowledge_base = self.knowledge_service.create_base(
            tenant_id=tenant_id,
            user_id=owner_user_id,
            request=KnowledgeBaseCreate(
                workspace_id=workspace_id,
                name=name,
                description="Starter knowledge space for pilot users.",
            ),
        )
        return knowledge_base.id

    def _seed_starter_solution_packs(
        self,
        tenant_id: str,
        owner_user_id: str,
        workspace_id: str,
        pack_ids: list[str],
    ) -> list[str]:
        if self.solution_pack_service is None or pack_ids == []:
            return []
        installed_skill_ids: list[str] = []
        seen_pack_ids: set[str] = set()
        for pack_id in pack_ids:
            if pack_id in seen_pack_ids:
                continue
            seen_pack_ids.add(pack_id)
            installation = self._get_or_install_starter_solution_pack(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                pack_id=pack_id,
            )
            for skill_id in installation.installed_skill_ids:
                if skill_id not in installed_skill_ids:
                    installed_skill_ids.append(skill_id)
        return installed_skill_ids

    def _get_or_install_starter_solution_pack(
        self,
        tenant_id: str,
        owner_user_id: str,
        workspace_id: str,
        pack_id: str,
    ):
        try:
            return self.solution_pack_service.pack_registry.get_installation(
                tenant_id,
                pack_id,
            )
        except NotFoundError:
            return self.solution_pack_service.install_for_tenant(
                tenant_id=tenant_id,
                pack_id=pack_id,
                workspace_ids=[workspace_id],
                installed_by_user_id=owner_user_id,
            )

    def _record_bootstrap_completed_once(
        self,
        tenant_id: str,
        tenant_slug: str,
        owner_user_id: str,
        owner_role: Role,
        starter_workspace_id: str,
        starter_knowledge_base_id: str,
        starter_skill_ids: list[str],
        starter_solution_pack_ids: list[str],
        starter_solution_pack_skill_ids: list[str],
    ) -> None:
        existing_events = [
            event
            for event in self.store.list_audit_events(tenant_id)
            if event.event_type == "tenant.bootstrap.completed"
        ]
        if existing_events != []:
            return
        self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=owner_user_id,
            run_id=None,
            event_type="tenant.bootstrap.completed",
            metadata={
                "tenant_slug": tenant_slug,
                "owner_user_id": owner_user_id,
                "owner_role_id": owner_role.id,
                "permissions_count": len(owner_role.permissions),
                "starter_workspace_id": starter_workspace_id,
                "starter_knowledge_base_id": starter_knowledge_base_id,
                "starter_skill_ids": starter_skill_ids,
                "starter_solution_pack_ids": starter_solution_pack_ids,
                "starter_solution_pack_skill_count": len(starter_solution_pack_skill_ids),
            },
        )

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
