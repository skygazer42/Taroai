import base64
import binascii
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import new_id, utc_now


class SandboxNetworkMode(str, Enum):
    DISABLED = "disabled"
    ALLOWLIST = "allowlist"
    OPEN = "open"


class SandboxSessionStatus(str, Enum):
    ACTIVE = "active"
    DESTROYED = "destroyed"


class SandboxCommandStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SandboxCreateRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    image: str = Field(default="python:3.12-slim", min_length=1)
    network_mode: SandboxNetworkMode = SandboxNetworkMode.DISABLED
    timeout_seconds: int = Field(default=300, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SandboxSessionCreateRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    image: str | None = None
    network_mode: SandboxNetworkMode | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)


class SandboxSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sandbox"))
    tenant_id: str
    workspace_id: str
    run_id: str
    provider: str
    image: str
    network_mode: SandboxNetworkMode
    timeout_seconds: int
    status: SandboxSessionStatus = SandboxSessionStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    destroyed_at: datetime | None = None


class SandboxCommand(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sandbox_command"))
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    command: str = Field(min_length=1)
    cwd: str = "/workspace"
    timeout_seconds: int = Field(default=300, ge=1)
    env: dict[str, str] = Field(default_factory=dict)


class SandboxCommandRequest(BaseModel):
    command: str = Field(min_length=1)
    cwd: str = "/workspace"
    timeout_seconds: int | None = Field(default=None, ge=1)
    env: dict[str, str] = Field(default_factory=dict)


class SandboxCommandResult(BaseModel):
    tenant_id: str
    workspace_id: str
    run_id: str
    session_id: str
    command: str
    exit_code: int
    status: SandboxCommandStatus = SandboxCommandStatus.SUCCEEDED
    stdout: str = ""
    stderr: str = ""
    output_uri: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SandboxFileWrite(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    content: str = ""
    content_base64: str | None = None
    content_type: str = "text/plain"

    def content_bytes(self) -> bytes:
        if self.content_base64 is None:
            return self.content.encode("utf-8")
        if self.content:
            raise ValueError("sandbox file write cannot include text and base64 content together")
        try:
            return base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("sandbox file write content_base64 is invalid") from error


class SandboxFileWriteRequest(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""
    content_base64: str | None = None
    content_type: str = "text/plain"


class SandboxFileRef(BaseModel):
    tenant_id: str
    workspace_id: str
    run_id: str
    session_id: str
    path: str
    content_type: str = "text/plain"
    size_bytes: int = 0
    content: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SandboxSnapshot(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sandbox_snapshot"))
    tenant_id: str
    workspace_id: str
    run_id: str
    session_id: str
    uri: str
    created_at: datetime = Field(default_factory=utc_now)


class SandboxControllerCapabilities(BaseModel):
    provider: str = Field(min_length=1)
    network_isolation: bool = False
    filesystem_isolation: bool = False
    resource_limits: bool = False
    destroy_supported: bool = False
    command_cancellation_supported: bool = False
    session_ttl_enforced: bool = False
    runtime_isolation: bool = False
    image_policy_enforced: bool = False
    allowed_image_count: int | None = Field(default=None, ge=0)
    max_session_ttl_seconds: int | None = Field(default=None, ge=1)
    max_sessions: int | None = Field(default=None, ge=1)
    max_sessions_per_tenant: int | None = Field(default=None, ge=1)
    max_sessions_per_run: int | None = Field(default=None, ge=1)


class BrowserActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SCREENSHOT = "screenshot"
    EXTRACT = "extract"


class BrowserSession(BaseModel):
    session_id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    current_url: str | None = None
    actions: list["BrowserAction"] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class BrowserAction(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    action_type: BrowserActionType
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserActionRequest(BaseModel):
    action_type: BrowserActionType
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserObservation(BaseModel):
    tenant_id: str
    workspace_id: str
    run_id: str
    session_id: str
    action_type: BrowserActionType
    current_url: str | None = None
    text: str | None = None
    screenshot_uri: str | None = None
    screenshot_content: bytes | None = Field(default=None, exclude=True)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
