import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from taroai.audit import AuditEventCreate
from taroai.domain import new_id, utc_now
from taroai.errors import NotFoundError
from taroai.identity import Permission, Role, UserAccountCreate
from taroai.identity.models import normalize_email


TENANT_MEMBER_ROLE_ID = "tenant_member"
TENANT_MEMBER_ROLE_NAME = "Tenant Member"
TENANT_INVITATION_TTL = timedelta(hours=72)


def tenant_member_permissions(tenant_id: str) -> list[Permission]:
    resource = f"tenant:{tenant_id}"
    return [
        Permission(action=action, resource=resource)
        for action in [
            "skills.read",
            "skills.invoke",
            "connectors.read",
            "connectors.invoke",
            "triggers.read",
            "triggers.invoke",
            "storage.read",
            "storage.write",
            "memory.read",
            "memory.write",
            "memory.review",
            "knowledge.read",
            "knowledge.write",
            "sandbox.create",
            "sandbox.execute",
            "browser.act",
            "model_policy.read",
            "model_providers.read",
            "solution_packs.read",
            "sharing.read",
            "sharing.manage",
            "customer_success.feedback",
        ]
    ]


def ensure_tenant_member_role(identity_service: Any, tenant_id: str) -> Role:
    try:
        return identity_service.get_role(tenant_id, TENANT_MEMBER_ROLE_ID)
    except NotFoundError:
        return identity_service.create_role(
            Role(
                tenant_id=tenant_id,
                id=TENANT_MEMBER_ROLE_ID,
                name=TENANT_MEMBER_ROLE_NAME,
                permissions=tenant_member_permissions(tenant_id),
            )
        )


class TenantInfo(BaseModel):
    id: str
    name: str


class WorkspaceInfo(BaseModel):
    id: str
    tenant_id: str
    name: str


class TenantMember(BaseModel):
    id: str
    email: str
    display_name: str
    status: str
    role_ids: list[str] = Field(default_factory=list)
    is_owner: bool = False


InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


class TenantInvitation(BaseModel):
    id: str
    tenant_id: str
    email: str
    invited_by_user_id: str
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    accepted_by_user_id: str | None = None


class TenantInvitationRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("invitation"))
    tenant_id: str
    email: str
    token_hash: str = Field(repr=False)
    invited_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    accepted_by_user_id: str | None = None

    def public(self, now: datetime | None = None) -> TenantInvitation:
        current = now or utc_now()
        if self.accepted_at is not None:
            status: InvitationStatus = "accepted"
        elif self.revoked_at is not None:
            status = "revoked"
        elif self.expires_at <= current:
            status = "expired"
        else:
            status = "pending"
        return TenantInvitation(
            **self.model_dump(exclude={"token_hash"}),
            status=status,
        )


class TenantSummary(BaseModel):
    tenant: TenantInfo
    workspaces: list[WorkspaceInfo]
    members: list[TenantMember]
    invitations: list[TenantInvitation]
    permissions: list[str] = Field(default_factory=list)
    can_manage: bool = False


class TenantPatch(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^.*\S.*$")


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^.*\S.*$")


class WorkspacePatch(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^.*\S.*$")


class TenantInvitationCreate(BaseModel):
    email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )


class TenantInvitationCreated(BaseModel):
    invitation: TenantInvitation
    token: str = Field(repr=False)


class TenantInvitationAccept(BaseModel):
    tenant_id: str = Field(min_length=1)
    token: str = Field(min_length=32, max_length=1024, repr=False)
    display_name: str = Field(min_length=1, max_length=120, pattern=r"^.*\S.*$")
    password: str = Field(min_length=8, max_length=1024, repr=False)


class TenantOrganizationService(BaseModel):
    store: Any
    identity_service: Any
    audit_service: Any
    token_secret: str = Field(min_length=1, repr=False)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def summary(self, tenant_id: str, current_user_id: str) -> TenantSummary:
        members = []
        for account in self.identity_service.list_users(tenant_id):
            role_ids = self.identity_service.list_role_ids_for_user(tenant_id, account.id)
            members.append(
                TenantMember(
                    id=account.id,
                    email=account.email,
                    display_name=account.display_name,
                    status=account.status,
                    role_ids=role_ids,
                    is_owner="tenant_owner" in role_ids,
                )
            )
        permissions = sorted(
            {
                permission.action
                for role_id in self.identity_service.list_role_ids_for_user(
                    tenant_id,
                    current_user_id,
                )
                for permission in self.identity_service.get_role(
                    tenant_id,
                    role_id,
                ).permissions
            }
        )
        can_manage = "organization.manage" in permissions
        return TenantSummary(
            tenant=self.store.get_tenant(tenant_id),
            workspaces=self.store.list_workspaces(tenant_id),
            members=members,
            invitations=(
                [
                    invitation.public()
                    for invitation in self.store.list_tenant_invitations(tenant_id)
                ]
                if can_manage
                else []
            ),
            permissions=permissions,
            can_manage=can_manage,
        )

    def rename_tenant(self, tenant_id: str, actor_user_id: str, name: str) -> TenantInfo:
        tenant = self.store.rename_tenant(tenant_id, name.strip())
        self._audit(tenant_id, actor_user_id, "organization.renamed", {"name": tenant.name})
        return tenant

    def create_workspace(
        self,
        tenant_id: str,
        actor_user_id: str,
        name: str,
    ) -> WorkspaceInfo:
        workspace = self.store.create_workspace(
            tenant_id,
            new_id("workspace"),
            actor_user_id,
            name.strip(),
        )
        self._audit(
            tenant_id,
            actor_user_id,
            "workspace.created",
            {"workspace_id": workspace.id, "name": workspace.name},
            workspace_id=workspace.id,
        )
        return workspace

    def rename_workspace(
        self,
        tenant_id: str,
        actor_user_id: str,
        workspace_id: str,
        name: str,
    ) -> WorkspaceInfo:
        workspace = self.store.rename_workspace(tenant_id, workspace_id, name.strip())
        self._audit(
            tenant_id,
            actor_user_id,
            "workspace.renamed",
            {"workspace_id": workspace.id, "name": workspace.name},
            workspace_id=workspace.id,
        )
        return workspace

    def invite(
        self,
        tenant_id: str,
        actor_user_id: str,
        email: str,
        now: datetime | None = None,
    ) -> TenantInvitationCreated:
        normalized_email = normalize_email(email)
        try:
            self.identity_service.get_user_by_email(tenant_id, normalized_email)
        except NotFoundError:
            pass
        else:
            raise ValueError("User is already a tenant member")
        current = now or utc_now()
        if any(
            item.email == normalized_email and item.public(current).status == "pending"
            for item in self.store.list_tenant_invitations(tenant_id)
        ):
            raise ValueError("A pending invitation already exists for this email")
        token = secrets.token_urlsafe(32)
        invitation = self.store.create_tenant_invitation(
            TenantInvitationRecord(
                tenant_id=tenant_id,
                email=normalized_email,
                token_hash=self._token_hash(tenant_id, token),
                invited_by_user_id=actor_user_id,
                created_at=current,
                expires_at=current + TENANT_INVITATION_TTL,
            )
        )
        self._audit(
            tenant_id,
            actor_user_id,
            "organization.invitation.created",
            {"invitation_id": invitation.id, "email": invitation.email},
        )
        return TenantInvitationCreated(invitation=invitation.public(current), token=token)

    def revoke_invitation(
        self,
        tenant_id: str,
        actor_user_id: str,
        invitation_id: str,
    ) -> TenantInvitation:
        invitation = self.store.revoke_tenant_invitation(
            tenant_id,
            invitation_id,
            utc_now(),
        )
        self._audit(
            tenant_id,
            actor_user_id,
            "organization.invitation.revoked",
            {"invitation_id": invitation.id, "email": invitation.email},
        )
        return invitation.public()

    def accept_invitation(self, request: TenantInvitationAccept) -> TenantMember:
        invitation = self.store.get_tenant_invitation_by_token_hash(
            request.tenant_id,
            self._token_hash(request.tenant_id, request.token),
        )
        if invitation.public().status != "pending":
            raise ValueError("Tenant invitation is no longer valid")
        try:
            account = self.identity_service.create_user(
                UserAccountCreate(
                    tenant_id=request.tenant_id,
                    email=invitation.email,
                    display_name=request.display_name.strip(),
                    password=request.password,
                )
            )
        except ValueError:
            account = self.identity_service.get_user_by_email(
                request.tenant_id,
                invitation.email,
            )
            if account.status != "active" or not self.identity_service.verify_password(
                request.tenant_id,
                invitation.email,
                request.password,
            ):
                raise ValueError("Tenant invitation acceptance credentials do not match")
        role = ensure_tenant_member_role(self.identity_service, request.tenant_id)
        if role.id not in self.identity_service.list_role_ids_for_user(
            request.tenant_id,
            account.id,
        ):
            self.identity_service.assign_role(request.tenant_id, account.id, role.id)
        accepted = self.store.accept_tenant_invitation(
            request.tenant_id,
            invitation.id,
            account.id,
            utc_now(),
        )
        self._audit(
            request.tenant_id,
            account.id,
            "organization.invitation.accepted",
            {"invitation_id": accepted.id, "member_user_id": account.id},
        )
        return TenantMember(
            id=account.id,
            email=account.email,
            display_name=account.display_name,
            status=account.status,
            role_ids=[role.id],
        )

    def remove_member(
        self,
        tenant_id: str,
        actor_user_id: str,
        user_id: str,
    ) -> TenantMember:
        if user_id == actor_user_id:
            raise ValueError("You cannot remove yourself")
        role_ids = self.identity_service.list_role_ids_for_user(tenant_id, user_id)
        if "tenant_owner" in role_ids:
            raise ValueError("Tenant owners cannot be removed")
        account = self.identity_service.disable_user(tenant_id, user_id)
        self._audit(
            tenant_id,
            actor_user_id,
            "organization.member.removed",
            {"member_user_id": user_id},
        )
        return TenantMember(
            id=account.id,
            email=account.email,
            display_name=account.display_name,
            status=account.status,
            role_ids=role_ids,
        )

    def restore_member(
        self,
        tenant_id: str,
        actor_user_id: str,
        user_id: str,
    ) -> TenantMember:
        account = self.identity_service.activate_user(tenant_id, user_id)
        role_ids = self.identity_service.list_role_ids_for_user(tenant_id, user_id)
        self._audit(
            tenant_id,
            actor_user_id,
            "organization.member.restored",
            {"member_user_id": user_id},
        )
        return TenantMember(
            id=account.id,
            email=account.email,
            display_name=account.display_name,
            status=account.status,
            role_ids=role_ids,
            is_owner="tenant_owner" in role_ids,
        )

    def _token_hash(self, tenant_id: str, token: str) -> str:
        message = f"tenant-invitation:v1:{tenant_id}:{token}".encode("utf-8")
        return hmac.new(
            self.token_secret.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()

    def _audit(
        self,
        tenant_id: str,
        user_id: str,
        event_type: str,
        metadata: dict[str, Any],
        workspace_id: str | None = None,
    ) -> None:
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                event_type=event_type,
                metadata=metadata,
            )
        )
