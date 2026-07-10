from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from taroai.domain import utc_now


class ShareResourceType(str, Enum):
    RUN = "run"
    ARTIFACT = "artifact"
    SKILL = "skill"
    KNOWLEDGE_SPACE = "knowledge_space"
    MEMORY_CANDIDATE = "memory_candidate"
    WORKSPACE = "workspace"
    AGENT_TEMPLATE = "agent_template"


class ShareSubjectType(str, Enum):
    USER = "user"
    GROUP = "group"
    WORKSPACE = "workspace"
    TENANT = "tenant"
    EXTERNAL_LINK = "external_link"


class SharePermission(str, Enum):
    VIEW = "view"
    COMMENT = "comment"
    USE = "use"
    COPY = "copy"
    EDIT = "edit"
    PUBLISH = "publish"
    ADMIN = "admin"


class ShareGrantStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


RESOURCE_PERMISSION_MATRIX: dict[ShareResourceType, set[SharePermission]] = {
    ShareResourceType.RUN: {
        SharePermission.VIEW,
        SharePermission.COMMENT,
        SharePermission.EDIT,
        SharePermission.ADMIN,
    },
    ShareResourceType.ARTIFACT: {
        SharePermission.VIEW,
        SharePermission.COMMENT,
        SharePermission.COPY,
        SharePermission.EDIT,
        SharePermission.ADMIN,
    },
    ShareResourceType.SKILL: {
        SharePermission.VIEW,
        SharePermission.USE,
        SharePermission.COPY,
        SharePermission.EDIT,
        SharePermission.PUBLISH,
        SharePermission.ADMIN,
    },
    ShareResourceType.KNOWLEDGE_SPACE: {
        SharePermission.VIEW,
        SharePermission.USE,
        SharePermission.EDIT,
        SharePermission.ADMIN,
    },
    ShareResourceType.MEMORY_CANDIDATE: {
        SharePermission.VIEW,
        SharePermission.EDIT,
        SharePermission.ADMIN,
    },
    ShareResourceType.WORKSPACE: {
        SharePermission.VIEW,
        SharePermission.COMMENT,
        SharePermission.ADMIN,
    },
    ShareResourceType.AGENT_TEMPLATE: {
        SharePermission.VIEW,
        SharePermission.USE,
        SharePermission.COPY,
        SharePermission.EDIT,
        SharePermission.PUBLISH,
        SharePermission.ADMIN,
    },
}
EXTERNAL_LINK_TOKEN_MIN_LENGTH = 32


def validate_external_link_subject_id(
    subject_type: ShareSubjectType,
    subject_id: str,
) -> None:
    if subject_type != ShareSubjectType.EXTERNAL_LINK:
        return
    if subject_id.startswith("hmac-sha256:"):
        return
    if len(subject_id) < EXTERNAL_LINK_TOKEN_MIN_LENGTH:
        raise ValueError(
            "external_link subject_id must be at least "
            f"{EXTERNAL_LINK_TOKEN_MIN_LENGTH} characters"
        )


def validate_external_link_permission(
    subject_type: ShareSubjectType,
    permission: SharePermission,
) -> None:
    if (
        subject_type == ShareSubjectType.EXTERNAL_LINK
        and permission != SharePermission.VIEW
    ):
        raise ValueError("external_link grants only support view permission")


def validate_external_link_resource(
    subject_type: ShareSubjectType,
    resource_type: ShareResourceType,
) -> None:
    if (
        subject_type == ShareSubjectType.EXTERNAL_LINK
        and resource_type != ShareResourceType.ARTIFACT
    ):
        raise ValueError("external_link grants only support artifact resources")


class ShareGrantCreate(BaseModel):
    tenant_id: str = Field(min_length=1)
    resource_type: ShareResourceType
    resource_id: str = Field(min_length=1)
    subject_type: ShareSubjectType
    subject_id: str = Field(min_length=1)
    permission: SharePermission
    created_by_user_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=1000)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_grant_semantics(self):
        if (
            self.subject_type == ShareSubjectType.EXTERNAL_LINK
            and self.expires_at is None
        ):
            raise ValueError("external_link grants require expires_at")
        validate_external_link_subject_id(self.subject_type, self.subject_id)
        validate_external_link_permission(self.subject_type, self.permission)
        validate_external_link_resource(self.subject_type, self.resource_type)
        allowed_permissions = RESOURCE_PERMISSION_MATRIX[self.resource_type]
        if self.permission not in allowed_permissions:
            raise ValueError(
                f"{self.permission.value} is not supported for {self.resource_type.value}"
            )
        return self


class ShareGrantApiCreate(BaseModel):
    resource_type: ShareResourceType
    resource_id: str = Field(min_length=1)
    subject_type: ShareSubjectType
    subject_id: str = Field(min_length=1)
    permission: SharePermission
    reason: str | None = Field(default=None, max_length=1000)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_api_grant_semantics(self):
        validate_external_link_subject_id(self.subject_type, self.subject_id)
        validate_external_link_permission(self.subject_type, self.permission)
        validate_external_link_resource(self.subject_type, self.resource_type)
        return self

    def to_create(self, tenant_id: str, created_by_user_id: str) -> ShareGrantCreate:
        return ShareGrantCreate(
            tenant_id=tenant_id,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
            permission=self.permission,
            created_by_user_id=created_by_user_id,
            reason=self.reason,
            expires_at=self.expires_at,
        )


class ShareGrantRevokeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class ShareGrant(BaseModel):
    id: str
    tenant_id: str
    resource_type: ShareResourceType
    resource_id: str
    subject_type: ShareSubjectType
    subject_id: str
    permission: SharePermission
    status: ShareGrantStatus = ShareGrantStatus.ACTIVE
    reason: str | None = None
    expires_at: datetime | None = None
    created_by_user_id: str
    created_at: datetime
    revoked_by_user_id: str | None = None
    revoked_at: datetime | None = None

    def is_active(self, now: datetime | None = None) -> bool:
        effective_now = now or utc_now()
        if self.status != ShareGrantStatus.ACTIVE:
            return False
        if self.expires_at is not None and self.expires_at <= effective_now:
            return False
        return True

    def grants_permission(self, permission: SharePermission) -> bool:
        return self.permission == permission or self.permission == SharePermission.ADMIN

    def matches_subject(
        self,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None = None,
        group_ids: list[str] | None = None,
        external_link_id: str | None = None,
    ) -> bool:
        if self.subject_type == ShareSubjectType.USER:
            return self.subject_id == user_id
        if self.subject_type == ShareSubjectType.GROUP:
            return self.subject_id in (group_ids or [])
        if self.subject_type == ShareSubjectType.WORKSPACE:
            return self.subject_id == workspace_id
        if self.subject_type == ShareSubjectType.TENANT:
            return self.subject_id == tenant_id
        if self.subject_type == ShareSubjectType.EXTERNAL_LINK:
            return self.subject_id == external_link_id
        return False


def share_grant_audit_metadata(grant: ShareGrant) -> dict:
    subject_id = grant.subject_id
    external_link_id_present = None
    if grant.subject_type == ShareSubjectType.EXTERNAL_LINK:
        subject_id = "[REDACTED]"
        external_link_id_present = True
    return {
        "grant_id": grant.id,
        "resource_type": grant.resource_type.value,
        "resource_id": grant.resource_id,
        "subject_type": grant.subject_type.value,
        "subject_id": subject_id,
        "permission": grant.permission.value,
        "status": grant.status.value,
        "expires_at": (
            grant.expires_at.isoformat() if grant.expires_at is not None else None
        ),
        "external_link_id_present": external_link_id_present,
    }
