from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from taroai.domain import new_id, utc_now


class StoragePurpose(str, Enum):
    ARTIFACT = "artifacts"
    UPLOAD = "uploads"
    KNOWLEDGE_DOCUMENT = "knowledge-documents"
    SANDBOX_FILE = "sandbox-files"
    SANDBOX_COMMAND_OUTPUT = "sandbox-command-outputs"
    SANDBOX_SNAPSHOT = "sandbox-snapshots"
    BROWSER_SCREENSHOT = "browser"
    DATA_EXPORT = "data-exports"


class StorageObjectCreate(BaseModel):
    tenant_id: str
    workspace_id: str | None = None
    run_id: str | None = None
    purpose: StoragePurpose
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    acl_subjects: list[str] = Field(default_factory=list)
    sensitivity_level: int = Field(default=0, ge=0)
    retention_expires_at: datetime | None = None


class StorageObjectApiCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    purpose: StoragePurpose
    filename: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    acl_subjects: list[str] = Field(default_factory=list)
    sensitivity_level: int = Field(default=0, ge=0)
    retention_expires_at: datetime | None = None


class StorageObjectPatch(BaseModel):
    filename: str = Field(min_length=1, max_length=512)


class StorageObject(BaseModel):
    id: str = Field(default_factory=lambda: new_id("storage"))
    tenant_id: str
    workspace_id: str | None = None
    run_id: str | None = None
    purpose: StoragePurpose
    filename: str
    content_type: str
    size_bytes: int
    acl_subjects: list[str] = Field(default_factory=list)
    sensitivity_level: int = 0
    bucket: str
    key: str
    retention_expires_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


class StorageUploadResult(BaseModel):
    storage_object_id: str
    uri: str
    etag: str | None = None


class StorageDownloadResult(BaseModel):
    storage_object_id: str
    uri: str
    content: bytes
    content_type: str


class StorageDeleteResult(BaseModel):
    storage_object_id: str
    uri: str
    delete_marker: bool = False


class StorageSignedUrl(BaseModel):
    storage_object_id: str
    tenant_id: str
    url: str
    method: Literal["GET", "PUT"]
    expires_at: datetime


class StorageSignedUrlCreate(BaseModel):
    operation: Literal["read", "write"]
    expires_in_seconds: int | None = Field(default=None, ge=1)
