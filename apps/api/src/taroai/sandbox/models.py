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
    content_type: str = "text/plain"


class SandboxFileWriteRequest(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""
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
