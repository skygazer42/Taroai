from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.domain import new_id, utc_now


class ConnectorType(str, Enum):
    SAAS = "saas"
    DATABASE = "database"
    FILE_STORE = "file_store"
    INTERNAL_API = "internal_api"
    MCP_SERVER = "mcp_server"
    WEB = "web"


class ConnectorAuthMode(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    SERVICE_ACCOUNT = "service_account"
    DATABASE_PASSWORD = "database_password"
    MCP = "mcp"


class ConnectorStatus(str, Enum):
    DRAFT = "draft"
    ENABLED = "enabled"
    DISABLED = "disabled"
    NEEDS_REAUTH = "needs_reauth"


class ConnectorSyncStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ConnectorSyncState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ConnectorSyncStatus
    run_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    cursor: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None


class ConnectorSyncStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ConnectorSyncStatus
    run_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    cursor: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None

    def to_state(self) -> ConnectorSyncState:
        return ConnectorSyncState(**self.model_dump())


ConnectorCapabilityRisk = Literal["low", "medium", "high", "critical"]


class ConnectorCredentialRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = None
    secret_ref_id: str = Field(min_length=1)
    required_actions: list[str] = Field(default_factory=list)


class ConnectorCredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret_ref_id: str = Field(min_length=1)
    required_actions: list[str] = Field(default_factory=list)


class ConnectorCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    required_scopes: list[str] = Field(default_factory=list)
    risk_level: ConnectorCapabilityRisk = "low"
    approval_required: bool = False
    enabled: bool = True


class ConnectorDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    type: ConnectorType
    display_name: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    auth_mode: ConnectorAuthMode
    credential_ref: ConnectorCredentialRef | None = None
    capabilities: list[ConnectorCapability] = Field(default_factory=list)
    sensitivity_level: int = Field(default=0, ge=0)
    status: ConnectorStatus = ConnectorStatus.DRAFT
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_auth_boundary(self) -> "ConnectorDefinitionCreate":
        if self.auth_mode != ConnectorAuthMode.NONE and self.credential_ref is None:
            raise ValueError("credential_ref is required for authenticated connectors")
        if self.auth_mode == ConnectorAuthMode.NONE and self.credential_ref is not None:
            raise ValueError("credential_ref is not allowed for unauthenticated connectors")
        return self


class ConnectorCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    type: ConnectorType
    display_name: str = Field(min_length=1)
    auth_mode: ConnectorAuthMode
    credential: ConnectorCredentialCreate | None = None
    capabilities: list[ConnectorCapability] = Field(default_factory=list)
    sensitivity_level: int = Field(default=0, ge=0)
    status: ConnectorStatus = ConnectorStatus.DRAFT
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_auth_boundary(self) -> "ConnectorCreateRequest":
        if self.auth_mode != ConnectorAuthMode.NONE and self.credential is None:
            raise ValueError("credential is required for authenticated connectors")
        if self.auth_mode == ConnectorAuthMode.NONE and self.credential is not None:
            raise ValueError("credential is not allowed for unauthenticated connectors")
        return self

    def to_definition_create(
        self,
        tenant_id: str,
        owner_user_id: str,
    ) -> ConnectorDefinitionCreate:
        credential_ref = None
        if self.credential is not None:
            credential_ref = ConnectorCredentialRef(
                tenant_id=tenant_id,
                workspace_id=self.workspace_id,
                secret_ref_id=self.credential.secret_ref_id,
                required_actions=self.credential.required_actions,
            )
        return ConnectorDefinitionCreate(
            tenant_id=tenant_id,
            workspace_id=self.workspace_id,
            type=self.type,
            display_name=self.display_name,
            owner_user_id=owner_user_id,
            auth_mode=self.auth_mode,
            credential_ref=credential_ref,
            capabilities=self.capabilities,
            sensitivity_level=self.sensitivity_level,
            status=self.status,
            metadata=self.metadata,
        )


class ConnectorUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1)
    capabilities: list[ConnectorCapability] | None = None
    sensitivity_level: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] | None = None

    def update_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name in (
            "display_name",
            "capabilities",
            "sensitivity_level",
            "metadata",
        ):
            if field_name in self.model_fields_set:
                values[field_name] = getattr(self, field_name)
        return values


class ConnectorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("connector"))
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    type: ConnectorType
    display_name: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    auth_mode: ConnectorAuthMode
    credential_ref: ConnectorCredentialRef | None = None
    capabilities: list[ConnectorCapability] = Field(default_factory=list)
    sensitivity_level: int = Field(default=0, ge=0)
    status: ConnectorStatus = ConnectorStatus.DRAFT
    metadata: dict[str, Any] = Field(default_factory=dict)
    sync_state: ConnectorSyncState | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_create(
        cls,
        create: ConnectorDefinitionCreate,
        created_at: datetime | None = None,
    ) -> "ConnectorDefinition":
        resolved_created_at = created_at or utc_now()
        return cls(
            tenant_id=create.tenant_id,
            workspace_id=create.workspace_id,
            type=create.type,
            display_name=create.display_name,
            owner_user_id=create.owner_user_id,
            auth_mode=create.auth_mode,
            credential_ref=create.credential_ref,
            capabilities=create.capabilities,
            sensitivity_level=create.sensitivity_level,
            status=create.status,
            metadata=create.metadata,
            sync_state=None,
            created_at=resolved_created_at,
            updated_at=resolved_created_at,
        )

    def apply_update(
        self,
        update: ConnectorUpdateRequest,
        updated_at: datetime | None = None,
    ) -> "ConnectorDefinition":
        values = update.update_values()
        if values:
            values["updated_at"] = updated_at or utc_now()
        return self.model_copy(update=values, deep=True)

    def apply_status(
        self,
        status: ConnectorStatus,
        updated_at: datetime | None = None,
    ) -> "ConnectorDefinition":
        return self.model_copy(
            update={
                "status": status,
                "updated_at": updated_at or utc_now(),
            },
            deep=True,
        )

    def apply_sync_state(
        self,
        update: ConnectorSyncStateUpdate,
        updated_at: datetime | None = None,
    ) -> "ConnectorDefinition":
        return self.model_copy(
            update={
                "sync_state": update.to_state(),
                "updated_at": updated_at or utc_now(),
            },
            deep=True,
        )
