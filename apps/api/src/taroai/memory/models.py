from datetime import datetime
from datetime import timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import new_id, utc_now


class MemoryScopeType(str, Enum):
    USER = "user"
    TEAM = "team"
    COMPANY = "company"
    AGENT = "agent"
    TASK = "task"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ShortTermMemoryReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MemoryWriteRequest(BaseModel):
    tenant_id: str
    workspace_id: str
    scope_type: MemoryScopeType
    scope_id: str
    source_run_id: str
    content: str = Field(min_length=1)
    created_by: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    sensitivity_level: int = 0
    confidence: float = 1.0
    expires_at: datetime | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE


class MemoryCandidateApiCreate(BaseModel):
    workspace_id: str
    scope_type: MemoryScopeType
    scope_id: str
    source_run_id: str
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    sensitivity_level: int = 0
    confidence: float = 1.0
    expires_at: datetime | None = None


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("memory"))
    tenant_id: str
    workspace_id: str
    scope_type: MemoryScopeType
    scope_id: str
    source_run_id: str
    content: str
    created_by: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    sensitivity_level: int = 0
    confidence: float = 1.0
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE


class ShortTermMemoryWrite(BaseModel):
    tenant_id: str
    workspace_id: str
    run_id: str
    key: str
    value: dict[str, Any]
    ttl_seconds: int = Field(default=3600, ge=1)
    created_by: str | None = None


class ShortTermMemoryApiCreate(BaseModel):
    workspace_id: str
    run_id: str
    key: str
    value: dict[str, Any]
    ttl_seconds: int = Field(default=3600, ge=1)


class ShortTermMemoryEntry(BaseModel):
    tenant_id: str
    workspace_id: str
    run_id: str
    key: str
    value: dict[str, Any]
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_write(cls, request: ShortTermMemoryWrite, now: datetime):
        return cls(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            key=request.key,
            value=request.value,
            created_at=now,
            expires_at=now + timedelta(seconds=request.ttl_seconds),
        )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class ShortTermMemoryReview(BaseModel):
    id: str = Field(default_factory=lambda: new_id("short_memory_review"))
    tenant_id: str
    workspace_id: str
    run_id: str
    key: str
    value: dict[str, Any]
    ttl_seconds: int = Field(default=3600, ge=1)
    created_by: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    status: ShortTermMemoryReviewStatus = ShortTermMemoryReviewStatus.PENDING
    approved_by_user_id: str | None = None
    approved_at: datetime | None = None
    rejected_by_user_id: str | None = None
    rejected_at: datetime | None = None
    activated_entry_expires_at: datetime | None = None
    guardrail_metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_write(
        cls,
        request: ShortTermMemoryWrite,
        now: datetime,
        guardrail_metadata: dict[str, Any],
    ):
        return cls(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            key=request.key,
            value=request.value,
            ttl_seconds=request.ttl_seconds,
            created_by=request.created_by,
            created_at=now,
            expires_at=now + timedelta(seconds=request.ttl_seconds),
            guardrail_metadata=guardrail_metadata,
        )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at
