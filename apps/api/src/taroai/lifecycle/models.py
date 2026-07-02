from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from taroai.domain import new_id, utc_now


class DataCategory(str, Enum):
    IDENTITY = "identity"
    RUN = "run"
    EVENT = "event"
    ARTIFACT = "artifact"
    STORAGE_OBJECT = "storage_object"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    VECTOR = "vector"
    AUDIT = "audit"
    BILLING = "billing"
    SANDBOX_SNAPSHOT = "sandbox_snapshot"
    CONNECTOR_CREDENTIAL_REF = "connector_credential_ref"
    TRACE = "trace"


class DeletionBehavior(str, Enum):
    RETAIN = "retain"
    TOMBSTONE = "tombstone"
    HARD_DELETE = "hard_delete"


class LegalHoldScopeType(str, Enum):
    TENANT = "tenant"
    WORKSPACE = "workspace"
    RUN = "run"
    STORAGE_OBJECT = "storage_object"
    USER = "user"
    KNOWLEDGE_BASE = "knowledge_base"


class LifecyclePolicyCreate(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = Field(default=None, min_length=1)
    category: DataCategory
    retention_days: int = Field(ge=1)
    deletion_behavior: DeletionBehavior
    exportable: bool
    residency_region: str = Field(min_length=1)
    backup_class: str = Field(min_length=1)
    legal_hold_supported: bool = True


class LifecyclePolicyApiUpsert(BaseModel):
    workspace_id: str | None = Field(default=None, min_length=1)
    retention_days: int = Field(ge=1)
    deletion_behavior: DeletionBehavior
    exportable: bool
    residency_region: str = Field(min_length=1)
    backup_class: str = Field(min_length=1)
    legal_hold_supported: bool = True


class LifecyclePolicy(LifecyclePolicyCreate):
    id: str = Field(default_factory=lambda: new_id("lifecycle_policy"))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LegalHoldCreate(BaseModel):
    tenant_id: str = Field(min_length=1)
    category: DataCategory
    scope_type: LegalHoldScopeType
    scope_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    created_by_user_id: str = Field(min_length=1)
    expires_at: datetime | None = None


class LegalHoldApiCreate(BaseModel):
    category: DataCategory
    scope_type: LegalHoldScopeType
    scope_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expires_at: datetime | None = None


class LegalHold(LegalHoldCreate):
    id: str = Field(default_factory=lambda: new_id("legal_hold"))
    released_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    def is_active(self, now: datetime) -> bool:
        if self.released_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= now:
            return False
        return True
