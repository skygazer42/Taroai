import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from taroai.domain import new_id, utc_now


def validate_git_ref(value: str) -> str:
    if (
        not value
        or value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "@{" in value
        or re.search(r"[\x00-\x20~^:?*\\\[]", value)
        or "//" in value
    ):
        raise ValueError("Branch is not a safe Git ref")
    return value


class RepositoryBindingCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    provider: Literal["github", "gitlab", "bitbucket", "generic"] = "github"
    repository_url: str = Field(min_length=1, max_length=2000)
    default_branch: str = Field(default="main", min_length=1, max_length=255)
    connector_id: str | None = Field(default=None, min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Repository URL must use HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Repository URL cannot contain credentials, query, or fragment")
        return value.rstrip("/")

    @field_validator("default_branch")
    @classmethod
    def validate_default_branch(cls, value: str) -> str:
        return validate_git_ref(value)


class RepositoryBindingPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    default_branch: str | None = Field(default=None, min_length=1, max_length=255)
    connector_id: str | None = None
    status: Literal["active", "disabled"] | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("default_branch")
    @classmethod
    def validate_default_branch(cls, value: str | None) -> str | None:
        return validate_git_ref(value) if value is not None else None


class RepositoryBinding(BaseModel):
    id: str = Field(default_factory=lambda: new_id("repository"))
    tenant_id: str
    workspace_id: str
    name: str
    provider: str
    repository_url: str
    default_branch: str = "main"
    connector_id: str | None = None
    status: Literal["active", "disabled"] = "active"
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CodingWorkspaceCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    engine_session_id: str | None = None
    branch: str | None = Field(default=None, max_length=255)
    base_revision: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid")

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str | None) -> str | None:
        return validate_git_ref(value) if value is not None else None


class CodingWorkspace(BaseModel):
    id: str = Field(default_factory=lambda: new_id("coding_workspace"))
    tenant_id: str
    workspace_id: str
    repository_id: str
    run_id: str
    engine_session_id: str | None = None
    branch: str
    worktree_path: str
    base_revision: str | None = None
    head_revision: str | None = None
    status: Literal["preparing", "ready", "dirty", "tested", "delivered", "failed", "closed"] = "preparing"
    created_by_user_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CodingChangeCreate(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    status: Literal["added", "modified", "deleted", "renamed", "untracked"]
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    patch: str = ""
    binary: bool = False
    previous_path: str | None = None


class CodingChange(CodingChangeCreate):
    id: str = Field(default_factory=lambda: new_id("code_change"))
    tenant_id: str
    coding_workspace_id: str
    created_at: datetime = Field(default_factory=utc_now)


class CodingChangesSubmit(BaseModel):
    head_revision: str | None = Field(default=None, max_length=255)
    changes: list[CodingChangeCreate] = Field(default_factory=list, max_length=5000)

    model_config = ConfigDict(extra="forbid")


class CodingTestResultCreate(BaseModel):
    command: str = Field(min_length=1, max_length=10_000)
    status: Literal["passed", "failed", "cancelled", "error"]
    duration_seconds: float = Field(default=0, ge=0)
    summary: str = Field(default="", max_length=20_000)
    output_artifact_id: str | None = None


class CodingTestResult(CodingTestResultCreate):
    id: str = Field(default_factory=lambda: new_id("code_test"))
    tenant_id: str
    coding_workspace_id: str
    created_at: datetime = Field(default_factory=utc_now)


class CodingCheckpointCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    revision: str = Field(min_length=1, max_length=255)
    snapshot_id: str | None = None


class CodingCheckpoint(CodingCheckpointCreate):
    id: str = Field(default_factory=lambda: new_id("code_checkpoint"))
    tenant_id: str
    coding_workspace_id: str
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)


class CodingDeliveryCreate(BaseModel):
    commit_sha: str | None = Field(default=None, max_length=255)
    commit_message: str | None = Field(default=None, max_length=10_000)
    pull_request_url: str | None = Field(default=None, max_length=2000)
    pull_request_number: str | None = Field(default=None, max_length=100)
    status: Literal["committed", "pull_request_open", "merged", "failed"]


class CodingDelivery(CodingDeliveryCreate):
    id: str = Field(default_factory=lambda: new_id("code_delivery"))
    tenant_id: str
    coding_workspace_id: str
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)


class CodingActionRequest(BaseModel):
    action: Literal["refresh", "test", "checkpoint", "commit", "pull_request", "rollback"]
    message: str | None = Field(default=None, max_length=10_000)
    command: str | None = Field(default=None, max_length=10_000)
    checkpoint_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
