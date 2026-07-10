import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import new_id, utc_now
from taroai.model_gateway.policy import ModelPolicyScope
from taroai.model_gateway.providers import (
    ModelProviderConfig,
    ModelProviderFallbackPolicy,
    ModelProviderRateLimit,
    validate_chat_request_options,
)
from taroai.store import NotFoundError


POLICY_VERSION_ALL_SCOPES = object()


class ModelPolicyScopeApiUpsert(BaseModel):
    workspace_id: str | None = None
    default_model: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    model_sensitivity_limits: dict[str, int] = Field(default_factory=dict)

    def to_upsert(
        self,
        tenant_id: str,
        updated_by_user_id: str,
    ) -> "ModelPolicyScopeUpsert":
        return ModelPolicyScopeUpsert(
            tenant_id=tenant_id,
            workspace_id=self.workspace_id,
            default_model=self.default_model,
            allowed_models=self.allowed_models,
            denied_models=self.denied_models,
            model_sensitivity_limits=self.model_sensitivity_limits,
            updated_by_user_id=updated_by_user_id,
        )


class ModelPolicyScopeUpsert(BaseModel):
    tenant_id: str
    workspace_id: str | None = None
    default_model: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    model_sensitivity_limits: dict[str, int] = Field(default_factory=dict)
    updated_by_user_id: str | None = None

    def to_policy_scope(self) -> ModelPolicyScope:
        return ModelPolicyScope(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            default_model=self.default_model,
            allowed_models=self.allowed_models,
            denied_models=self.denied_models,
            model_sensitivity_limits=self.model_sensitivity_limits,
        )


class ModelPolicyScopeRecord(BaseModel):
    tenant_id: str
    workspace_id: str | None = None
    default_model: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    model_sensitivity_limits: dict[str, int] = Field(default_factory=dict)
    updated_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def to_policy_scope(self) -> ModelPolicyScope:
        return ModelPolicyScope(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            default_model=self.default_model,
            allowed_models=self.allowed_models,
            denied_models=self.denied_models,
            model_sensitivity_limits=self.model_sensitivity_limits,
        )


class ModelPolicyVersionRecord(BaseModel):
    tenant_id: str
    workspace_id: str | None = None
    version: int = Field(ge=1)
    default_model: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    model_sensitivity_limits: dict[str, int] = Field(default_factory=dict)
    change_type: Literal["upsert_scope", "approved_change_request"] = "upsert_scope"
    change_request_id: str | None = None
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    def to_policy_scope(self) -> ModelPolicyScope:
        return ModelPolicyScope(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            default_model=self.default_model,
            allowed_models=self.allowed_models,
            denied_models=self.denied_models,
            model_sensitivity_limits=self.model_sensitivity_limits,
        )


class ModelPolicyChangePayload(BaseModel):
    scope_upsert: ModelPolicyScopeUpsert


class ModelPolicyChangeRequestApiCreate(BaseModel):
    scope: ModelPolicyScopeApiUpsert

    def to_create(
        self,
        tenant_id: str,
        requested_by_user_id: str,
    ) -> "ModelPolicyChangeRequestCreate":
        return ModelPolicyChangeRequestCreate(
            tenant_id=tenant_id,
            scope_upsert=self.scope.to_upsert(
                tenant_id=tenant_id,
                updated_by_user_id=requested_by_user_id,
            ),
            requested_by_user_id=requested_by_user_id,
        )


class ModelPolicyChangeRequestCreate(BaseModel):
    tenant_id: str
    operation: Literal["upsert_scope"] = "upsert_scope"
    scope_upsert: ModelPolicyScopeUpsert
    requested_by_user_id: str

    @model_validator(mode="after")
    def validate_scope_tenant(self) -> "ModelPolicyChangeRequestCreate":
        if self.scope_upsert.tenant_id != self.tenant_id:
            raise ValueError("model policy change scope tenant must match request tenant")
        return self

    def to_payload(self) -> ModelPolicyChangePayload:
        return ModelPolicyChangePayload(scope_upsert=self.scope_upsert)


class ModelPolicyChangeRequestRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("model_policy_change"))
    tenant_id: str
    operation: Literal["upsert_scope"] = "upsert_scope"
    status: Literal["pending", "approved", "rejected"] = "pending"
    scope_upsert: ModelPolicyScopeUpsert
    requested_by_user_id: str
    reviewed_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    reviewed_at: datetime | None = None

    def to_payload(self) -> ModelPolicyChangePayload:
        return ModelPolicyChangePayload(scope_upsert=self.scope_upsert)


class ModelPolicyChangeApplyResult(BaseModel):
    change_request: ModelPolicyChangeRequestRecord
    scope_record: ModelPolicyScopeRecord | None = None


class ModelPolicyStore(BaseModel):
    def upsert_scope(self, request: ModelPolicyScopeUpsert) -> ModelPolicyScopeRecord:
        raise NotImplementedError

    def list_scopes(self, tenant_id: str) -> list[ModelPolicyScopeRecord]:
        raise NotImplementedError

    def list_all_scopes(self) -> list[ModelPolicyScopeRecord]:
        raise NotImplementedError

    def list_policy_versions(
        self,
        tenant_id: str,
        workspace_id: str | None | object = POLICY_VERSION_ALL_SCOPES,
    ) -> list[ModelPolicyVersionRecord]:
        raise NotImplementedError

    def create_policy_change_request(
        self,
        request: ModelPolicyChangeRequestCreate,
    ) -> ModelPolicyChangeRequestRecord:
        raise NotImplementedError

    def list_policy_change_requests(
        self,
        tenant_id: str,
    ) -> list[ModelPolicyChangeRequestRecord]:
        raise NotImplementedError

    def approve_policy_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelPolicyChangeApplyResult:
        raise NotImplementedError

    def reject_policy_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelPolicyChangeRequestRecord:
        raise NotImplementedError



class ModelProviderApiUpsert(BaseModel):
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1)
    api_key_secret_ref_id: str = Field(min_length=1)
    secret_lease_ttl_seconds: int = Field(default=60, ge=1)
    default_model: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    workspace_id: str | None = None
    priority: int = Field(default=100, ge=0)
    timeout_seconds: int = Field(default=30, ge=1)
    chat_request_options: dict[str, Any] = Field(default_factory=dict)
    rate_limit: ModelProviderRateLimit = Field(default_factory=ModelProviderRateLimit)
    fallback_enabled: bool = True
    fallback_policy: ModelProviderFallbackPolicy = Field(
        default_factory=ModelProviderFallbackPolicy
    )

    @model_validator(mode="after")
    def validate_provider_chat_request_options(self) -> "ModelProviderApiUpsert":
        validate_chat_request_options(self.chat_request_options)
        return self

    def to_upsert(
        self,
        tenant_id: str,
        provider_id: str,
        updated_by_user_id: str,
    ) -> "ModelProviderUpsert":
        return ModelProviderUpsert(
            tenant_id=tenant_id,
            id=provider_id,
            provider_type=self.provider_type,
            base_url=self.base_url,
            api_key_secret_ref_id=self.api_key_secret_ref_id,
            secret_lease_ttl_seconds=self.secret_lease_ttl_seconds,
            default_model=self.default_model,
            model_ids=self.model_ids,
            workspace_id=self.workspace_id,
            priority=self.priority,
            timeout_seconds=self.timeout_seconds,
            chat_request_options=self.chat_request_options,
            rate_limit=self.rate_limit,
            fallback_enabled=self.fallback_enabled,
            fallback_policy=self.fallback_policy,
            updated_by_user_id=updated_by_user_id,
        )


class ModelProviderCredentialRotateRequest(BaseModel):
    api_key_secret_ref_id: str = Field(min_length=1)


class ModelProviderChangeRequestApiCreate(BaseModel):
    operation: Literal[
        "upsert",
        "disable",
        "enable",
        "credential_rotation",
        "rollback",
    ] = "upsert"
    provider: ModelProviderApiUpsert | None = None
    credential: ModelProviderCredentialRotateRequest | None = None
    rollback_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_operation_payload(self) -> "ModelProviderChangeRequestApiCreate":
        if self.operation == "upsert" and self.provider is None:
            raise ValueError("upsert provider change requests require provider")
        if self.operation == "credential_rotation" and self.credential is None:
            raise ValueError("credential_rotation change requests require credential")
        if self.operation == "rollback" and self.rollback_version is None:
            raise ValueError("rollback change requests require rollback_version")
        return self

    def to_create(
        self,
        tenant_id: str,
        provider_id: str,
        requested_by_user_id: str,
    ) -> "ModelProviderChangeRequestCreate":
        provider_upsert = None
        api_key_secret_ref_id = None
        status = None
        rollback_version = None
        if self.operation == "upsert" and self.provider is not None:
            provider_upsert = self.provider.to_upsert(
                tenant_id=tenant_id,
                provider_id=provider_id,
                updated_by_user_id=requested_by_user_id,
            )
        if self.operation == "credential_rotation" and self.credential is not None:
            api_key_secret_ref_id = self.credential.api_key_secret_ref_id
        if self.operation == "disable":
            status = "disabled"
        if self.operation == "enable":
            status = "active"
        if self.operation == "rollback":
            rollback_version = self.rollback_version
        return ModelProviderChangeRequestCreate(
            tenant_id=tenant_id,
            provider_id=provider_id,
            operation=self.operation,
            provider_upsert=provider_upsert,
            api_key_secret_ref_id=api_key_secret_ref_id,
            status=status,
            rollback_version=rollback_version,
            requested_by_user_id=requested_by_user_id,
        )


class ModelProviderUpsert(BaseModel):
    tenant_id: str
    id: str = Field(min_length=1)
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1)
    api_key_secret_ref_id: str = Field(min_length=1)
    secret_lease_ttl_seconds: int = Field(default=60, ge=1)
    default_model: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    workspace_id: str | None = None
    priority: int = Field(default=100, ge=0)
    timeout_seconds: int = Field(default=30, ge=1)
    chat_request_options: dict[str, Any] = Field(default_factory=dict)
    rate_limit: ModelProviderRateLimit = Field(default_factory=ModelProviderRateLimit)
    fallback_enabled: bool = True
    fallback_policy: ModelProviderFallbackPolicy = Field(
        default_factory=ModelProviderFallbackPolicy
    )
    updated_by_user_id: str | None = None

    @model_validator(mode="after")
    def validate_provider_chat_request_options(self) -> "ModelProviderUpsert":
        validate_chat_request_options(self.chat_request_options)
        return self

    def to_provider_config(self) -> ModelProviderConfig:
        return ModelProviderConfig(
            id=self.id,
            provider_type=self.provider_type,
            base_url=self.base_url,
            api_key_secret_ref_id=self.api_key_secret_ref_id,
            secret_lease_ttl_seconds=self.secret_lease_ttl_seconds,
            default_model=self.default_model,
            model_ids=self.model_ids,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            priority=self.priority,
            timeout_seconds=self.timeout_seconds,
            chat_request_options=self.chat_request_options,
            rate_limit=self.rate_limit,
            fallback_enabled=self.fallback_enabled,
            fallback_policy=self.fallback_policy,
        )


class ModelProviderChangePayload(BaseModel):
    provider_upsert: ModelProviderUpsert | None = None
    api_key_secret_ref_id: str | None = None
    status: Literal["active", "disabled"] | None = None
    rollback_version: int | None = Field(default=None, ge=1)


class ModelProviderRecord(BaseModel):
    tenant_id: str
    id: str
    provider: ModelProviderConfig
    status: Literal["active", "disabled"] = "active"
    current_version: int = 0
    updated_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def to_provider_config(self) -> ModelProviderConfig:
        return self.provider


class ModelProviderVersionRecord(BaseModel):
    tenant_id: str
    provider_id: str
    version: int = Field(ge=1)
    provider: ModelProviderConfig
    status: Literal["active", "disabled"] = "active"
    change_type: Literal[
        "upsert",
        "status",
        "credential_rotation",
        "rollback",
    ] = "upsert"
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ModelProviderChangeRequestCreate(BaseModel):
    tenant_id: str
    provider_id: str
    operation: Literal[
        "upsert",
        "disable",
        "enable",
        "credential_rotation",
        "rollback",
    ] = "upsert"
    provider_upsert: ModelProviderUpsert | None = None
    api_key_secret_ref_id: str | None = None
    status: Literal["active", "disabled"] | None = None
    rollback_version: int | None = Field(default=None, ge=1)
    requested_by_user_id: str

    def to_payload(self) -> ModelProviderChangePayload:
        return ModelProviderChangePayload(
            provider_upsert=self.provider_upsert,
            api_key_secret_ref_id=self.api_key_secret_ref_id,
            status=self.status,
            rollback_version=self.rollback_version,
        )


class ModelProviderChangeRequestRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("model_provider_change"))
    tenant_id: str
    provider_id: str
    operation: Literal[
        "upsert",
        "disable",
        "enable",
        "credential_rotation",
        "rollback",
    ] = "upsert"
    status: Literal["pending", "approved", "rejected"] = "pending"
    provider_upsert: ModelProviderUpsert | None = None
    api_key_secret_ref_id: str | None = None
    target_status: Literal["active", "disabled"] | None = None
    rollback_version: int | None = Field(default=None, ge=1)
    requested_by_user_id: str
    reviewed_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    reviewed_at: datetime | None = None

    def to_payload(self) -> ModelProviderChangePayload:
        return ModelProviderChangePayload(
            provider_upsert=self.provider_upsert,
            api_key_secret_ref_id=self.api_key_secret_ref_id,
            status=self.target_status,
            rollback_version=self.rollback_version,
        )


class ModelProviderChangeApplyResult(BaseModel):
    change_request: ModelProviderChangeRequestRecord
    provider_record: ModelProviderRecord | None = None


class ModelProviderStore(BaseModel):
    def upsert_provider(self, request: ModelProviderUpsert) -> ModelProviderRecord:
        raise NotImplementedError

    def list_providers(self, tenant_id: str) -> list[ModelProviderRecord]:
        raise NotImplementedError

    def list_all_providers(self) -> list[ModelProviderRecord]:
        raise NotImplementedError

    def get_provider(self, tenant_id: str, provider_id: str) -> ModelProviderRecord:
        raise NotImplementedError

    def set_status(
        self,
        tenant_id: str,
        provider_id: str,
        status: Literal["active", "disabled"],
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        raise NotImplementedError

    def rotate_credential(
        self,
        tenant_id: str,
        provider_id: str,
        api_key_secret_ref_id: str,
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        raise NotImplementedError

    def list_provider_versions(
        self,
        tenant_id: str,
        provider_id: str,
    ) -> list[ModelProviderVersionRecord]:
        raise NotImplementedError

    def rollback_provider_version(
        self,
        tenant_id: str,
        provider_id: str,
        version: int,
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        raise NotImplementedError

    def create_provider_change_request(
        self,
        request: ModelProviderChangeRequestCreate,
    ) -> ModelProviderChangeRequestRecord:
        raise NotImplementedError

    def list_provider_change_requests(
        self,
        tenant_id: str,
    ) -> list[ModelProviderChangeRequestRecord]:
        raise NotImplementedError

    def approve_provider_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelProviderChangeApplyResult:
        raise NotImplementedError

    def reject_provider_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelProviderChangeRequestRecord:
        raise NotImplementedError


class InMemoryModelProviderStore(ModelProviderStore):
    providers: dict[str, ModelProviderRecord] = Field(default_factory=dict)
    versions: dict[str, list[ModelProviderVersionRecord]] = Field(default_factory=dict)
    change_requests: dict[str, ModelProviderChangeRequestRecord] = Field(
        default_factory=dict
    )

    def upsert_provider(self, request: ModelProviderUpsert) -> ModelProviderRecord:
        provider = request.to_provider_config()
        now = utc_now()
        key = self._key(request.tenant_id, request.id)
        existing = self.providers.get(key)
        record = ModelProviderRecord(
            tenant_id=request.tenant_id,
            id=request.id,
            provider=provider,
            status=existing.status if existing is not None else "active",
            current_version=self._next_provider_version(request.tenant_id, request.id),
            updated_by_user_id=request.updated_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._save_record_version(record, "upsert", request.updated_by_user_id)
        return record

    def list_providers(self, tenant_id: str) -> list[ModelProviderRecord]:
        return self._sort_records(
            [record for record in self.providers.values() if record.tenant_id == tenant_id]
        )

    def list_all_providers(self) -> list[ModelProviderRecord]:
        return self._sort_records(list(self.providers.values()))

    def get_provider(self, tenant_id: str, provider_id: str) -> ModelProviderRecord:
        record = self.providers.get(self._key(tenant_id, provider_id))
        if record is None:
            raise NotFoundError(f"Model provider not found: {provider_id}")
        return record

    def set_status(
        self,
        tenant_id: str,
        provider_id: str,
        status: Literal["active", "disabled"],
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        existing = self.get_provider(tenant_id, provider_id)
        record = existing.model_copy(
            update={
                "status": status,
                "current_version": self._next_provider_version(tenant_id, provider_id),
                "updated_by_user_id": updated_by_user_id,
                "updated_at": utc_now(),
            }
        )
        self._save_record_version(record, "status", updated_by_user_id)
        return record

    def rotate_credential(
        self,
        tenant_id: str,
        provider_id: str,
        api_key_secret_ref_id: str,
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        existing = self.get_provider(tenant_id, provider_id)
        provider = existing.provider.model_copy(
            update={"api_key_secret_ref_id": api_key_secret_ref_id}
        )
        record = existing.model_copy(
            update={
                "provider": provider,
                "current_version": self._next_provider_version(tenant_id, provider_id),
                "updated_by_user_id": updated_by_user_id,
                "updated_at": utc_now(),
            }
        )
        self._save_record_version(record, "credential_rotation", updated_by_user_id)
        return record

    def list_provider_versions(
        self,
        tenant_id: str,
        provider_id: str,
    ) -> list[ModelProviderVersionRecord]:
        return list(self.versions.get(self._key(tenant_id, provider_id), []))

    def rollback_provider_version(
        self,
        tenant_id: str,
        provider_id: str,
        version: int,
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        existing = self.get_provider(tenant_id, provider_id)
        target = self._get_provider_version(tenant_id, provider_id, version)
        record = ModelProviderRecord(
            tenant_id=tenant_id,
            id=provider_id,
            provider=target.provider,
            status=target.status,
            current_version=self._next_provider_version(tenant_id, provider_id),
            updated_by_user_id=updated_by_user_id,
            created_at=existing.created_at,
            updated_at=utc_now(),
        )
        self._save_record_version(record, "rollback", updated_by_user_id)
        return record

    def create_provider_change_request(
        self,
        request: ModelProviderChangeRequestCreate,
    ) -> ModelProviderChangeRequestRecord:
        record = ModelProviderChangeRequestRecord(
            tenant_id=request.tenant_id,
            provider_id=request.provider_id,
            operation=request.operation,
            provider_upsert=request.provider_upsert,
            api_key_secret_ref_id=request.api_key_secret_ref_id,
            target_status=request.status,
            rollback_version=request.rollback_version,
            requested_by_user_id=request.requested_by_user_id,
        )
        self.change_requests[self._change_key(record.tenant_id, record.id)] = record
        return record

    def list_provider_change_requests(
        self,
        tenant_id: str,
    ) -> list[ModelProviderChangeRequestRecord]:
        return sorted(
            [
                record
                for record in self.change_requests.values()
                if record.tenant_id == tenant_id
            ],
            key=lambda record: (record.created_at, record.id),
        )

    def approve_provider_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelProviderChangeApplyResult:
        request = self._get_change_request(tenant_id, request_id)
        self._require_pending_change_request(request)
        provider_record = self._apply_provider_change_request(
            request,
            reviewed_by_user_id,
        )
        reviewed = request.model_copy(
            update={
                "status": "approved",
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self.change_requests[self._change_key(tenant_id, request_id)] = reviewed
        return ModelProviderChangeApplyResult(
            change_request=reviewed,
            provider_record=provider_record,
        )

    def reject_provider_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelProviderChangeRequestRecord:
        request = self._get_change_request(tenant_id, request_id)
        self._require_pending_change_request(request)
        reviewed = request.model_copy(
            update={
                "status": "rejected",
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self.change_requests[self._change_key(tenant_id, request_id)] = reviewed
        return reviewed

    def _apply_provider_change_request(
        self,
        request: ModelProviderChangeRequestRecord,
        reviewed_by_user_id: str,
    ) -> ModelProviderRecord:
        if request.operation == "upsert" and request.provider_upsert is not None:
            return self.upsert_provider(
                request.provider_upsert.model_copy(
                    update={"updated_by_user_id": reviewed_by_user_id}
                )
            )
        if request.operation in {"disable", "enable"} and request.target_status is not None:
            return self.set_status(
                tenant_id=request.tenant_id,
                provider_id=request.provider_id,
                status=request.target_status,
                updated_by_user_id=reviewed_by_user_id,
            )
        if (
            request.operation == "credential_rotation"
            and request.api_key_secret_ref_id is not None
        ):
            return self.rotate_credential(
                tenant_id=request.tenant_id,
                provider_id=request.provider_id,
                api_key_secret_ref_id=request.api_key_secret_ref_id,
                updated_by_user_id=reviewed_by_user_id,
            )
        if request.operation == "rollback" and request.rollback_version is not None:
            return self.rollback_provider_version(
                tenant_id=request.tenant_id,
                provider_id=request.provider_id,
                version=request.rollback_version,
                updated_by_user_id=reviewed_by_user_id,
            )
        raise ValueError(f"Invalid model provider change request: {request.id}")

    def _get_change_request(
        self,
        tenant_id: str,
        request_id: str,
    ) -> ModelProviderChangeRequestRecord:
        record = self.change_requests.get(self._change_key(tenant_id, request_id))
        if record is None:
            raise NotFoundError(f"Model provider change request not found: {request_id}")
        return record

    def _require_pending_change_request(
        self,
        request: ModelProviderChangeRequestRecord,
    ) -> None:
        if request.status != "pending":
            raise ValueError(f"Model provider change request is not pending: {request.id}")

    def _key(self, tenant_id: str, provider_id: str) -> str:
        return f"{tenant_id}:{provider_id}"

    def _change_key(self, tenant_id: str, request_id: str) -> str:
        return f"{tenant_id}:{request_id}"

    def _next_provider_version(self, tenant_id: str, provider_id: str) -> int:
        entries = self.versions.get(self._key(tenant_id, provider_id), [])
        if not entries:
            return 1
        return max(entry.version for entry in entries) + 1

    def _save_record_version(
        self,
        record: ModelProviderRecord,
        change_type: Literal["upsert", "status", "credential_rotation", "rollback"],
        created_by_user_id: str | None,
    ) -> None:
        key = self._key(record.tenant_id, record.id)
        self.providers[key] = record
        self.versions.setdefault(key, []).append(
            ModelProviderVersionRecord(
                tenant_id=record.tenant_id,
                provider_id=record.id,
                version=record.current_version,
                provider=record.provider,
                status=record.status,
                change_type=change_type,
                created_by_user_id=created_by_user_id,
                created_at=record.updated_at,
            )
        )

    def _get_provider_version(
        self,
        tenant_id: str,
        provider_id: str,
        version: int,
    ) -> ModelProviderVersionRecord:
        for entry in self.list_provider_versions(tenant_id, provider_id):
            if entry.version == version:
                return entry
        raise NotFoundError(f"Model provider version not found: {provider_id}@{version}")

    def _sort_records(
        self,
        records: list[ModelProviderRecord],
    ) -> list[ModelProviderRecord]:
        return sorted(records, key=lambda record: (record.tenant_id, record.id))


class SqlModelProviderStore(ModelProviderStore):
    config: DatabaseConfig

    def upsert_provider(self, request: ModelProviderUpsert) -> ModelProviderRecord:
        provider = request.to_provider_config()
        now = utc_now()
        with self._connect() as connection:
            existing = self._get_provider_optional_with_connection(
                connection,
                request.tenant_id,
                request.id,
            )
            record = ModelProviderRecord(
                tenant_id=request.tenant_id,
                id=request.id,
                provider=provider,
                status=existing.status if existing is not None else "active",
                current_version=self._next_provider_version(
                    connection,
                    request.tenant_id,
                    request.id,
                ),
                updated_by_user_id=request.updated_by_user_id,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
            self._save_record_with_connection(connection, record)
            self._append_version_with_connection(
                connection,
                record,
                "upsert",
                request.updated_by_user_id,
            )
        return record

    def list_providers(self, tenant_id: str) -> list[ModelProviderRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_provider_records
                WHERE tenant_id = ?
                ORDER BY provider_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._provider_record_from_row(row) for row in rows]

    def list_all_providers(self) -> list[ModelProviderRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_provider_records
                ORDER BY tenant_id, provider_id
                """
            ).fetchall()
        return [self._provider_record_from_row(row) for row in rows]

    def get_provider(self, tenant_id: str, provider_id: str) -> ModelProviderRecord:
        record = self._get_provider_optional(tenant_id, provider_id)
        if record is None:
            raise NotFoundError(f"Model provider not found: {provider_id}")
        return record

    def set_status(
        self,
        tenant_id: str,
        provider_id: str,
        status: Literal["active", "disabled"],
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        with self._connect() as connection:
            existing = self._get_provider_required_with_connection(
                connection,
                tenant_id,
                provider_id,
            )
            record = existing.model_copy(
                update={
                    "status": status,
                    "current_version": self._next_provider_version(
                        connection,
                        tenant_id,
                        provider_id,
                    ),
                    "updated_by_user_id": updated_by_user_id,
                    "updated_at": utc_now(),
                }
            )
            self._save_record_with_connection(connection, record)
            self._append_version_with_connection(
                connection,
                record,
                "status",
                updated_by_user_id,
            )
        return record

    def rotate_credential(
        self,
        tenant_id: str,
        provider_id: str,
        api_key_secret_ref_id: str,
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        with self._connect() as connection:
            existing = self._get_provider_required_with_connection(
                connection,
                tenant_id,
                provider_id,
            )
            provider = existing.provider.model_copy(
                update={"api_key_secret_ref_id": api_key_secret_ref_id}
            )
            record = existing.model_copy(
                update={
                    "provider": provider,
                    "current_version": self._next_provider_version(
                        connection,
                        tenant_id,
                        provider_id,
                    ),
                    "updated_by_user_id": updated_by_user_id,
                    "updated_at": utc_now(),
                }
            )
            self._save_record_with_connection(connection, record)
            self._append_version_with_connection(
                connection,
                record,
                "credential_rotation",
                updated_by_user_id,
            )
        return record

    def create_provider_change_request(
        self,
        request: ModelProviderChangeRequestCreate,
    ) -> ModelProviderChangeRequestRecord:
        record = ModelProviderChangeRequestRecord(
            tenant_id=request.tenant_id,
            provider_id=request.provider_id,
            operation=request.operation,
            provider_upsert=request.provider_upsert,
            api_key_secret_ref_id=request.api_key_secret_ref_id,
            target_status=request.status,
            rollback_version=request.rollback_version,
            requested_by_user_id=request.requested_by_user_id,
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, record.tenant_id)
            if record.provider_upsert is not None and record.provider_upsert.workspace_id is not None:
                self._ensure_workspace(
                    connection,
                    record.tenant_id,
                    record.provider_upsert.workspace_id,
                )
            self._save_change_request_with_connection(connection, record)
        return record

    def list_provider_change_requests(
        self,
        tenant_id: str,
    ) -> list[ModelProviderChangeRequestRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_provider_change_requests
                WHERE tenant_id = ?
                ORDER BY created_at, request_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._change_request_from_row(row) for row in rows]

    def approve_provider_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelProviderChangeApplyResult:
        request = self._get_change_request(tenant_id, request_id)
        self._require_pending_change_request(request)
        provider_record = self._apply_provider_change_request(
            request,
            reviewed_by_user_id,
        )
        reviewed = request.model_copy(
            update={
                "status": "approved",
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self._save_change_request(reviewed)
        return ModelProviderChangeApplyResult(
            change_request=reviewed,
            provider_record=provider_record,
        )

    def reject_provider_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelProviderChangeRequestRecord:
        request = self._get_change_request(tenant_id, request_id)
        self._require_pending_change_request(request)
        reviewed = request.model_copy(
            update={
                "status": "rejected",
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self._save_change_request(reviewed)
        return reviewed

    def _apply_provider_change_request(
        self,
        request: ModelProviderChangeRequestRecord,
        reviewed_by_user_id: str,
    ) -> ModelProviderRecord:
        if request.operation == "upsert" and request.provider_upsert is not None:
            return self.upsert_provider(
                request.provider_upsert.model_copy(
                    update={"updated_by_user_id": reviewed_by_user_id}
                )
            )
        if request.operation in {"disable", "enable"} and request.target_status is not None:
            return self.set_status(
                tenant_id=request.tenant_id,
                provider_id=request.provider_id,
                status=request.target_status,
                updated_by_user_id=reviewed_by_user_id,
            )
        if (
            request.operation == "credential_rotation"
            and request.api_key_secret_ref_id is not None
        ):
            return self.rotate_credential(
                tenant_id=request.tenant_id,
                provider_id=request.provider_id,
                api_key_secret_ref_id=request.api_key_secret_ref_id,
                updated_by_user_id=reviewed_by_user_id,
            )
        if request.operation == "rollback" and request.rollback_version is not None:
            return self.rollback_provider_version(
                tenant_id=request.tenant_id,
                provider_id=request.provider_id,
                version=request.rollback_version,
                updated_by_user_id=reviewed_by_user_id,
            )
        raise ValueError(f"Invalid model provider change request: {request.id}")

    def _save_change_request(
        self,
        record: ModelProviderChangeRequestRecord,
    ) -> None:
        with self._connect() as connection:
            self._save_change_request_with_connection(connection, record)

    def _save_change_request_with_connection(
        self,
        connection,
        record: ModelProviderChangeRequestRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO model_provider_change_requests (
                tenant_id, request_id, provider_id, operation, payload, status,
                requested_by_user_id, reviewed_by_user_id, created_at, reviewed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, request_id) DO UPDATE SET
                payload = excluded.payload,
                status = excluded.status,
                reviewed_by_user_id = excluded.reviewed_by_user_id,
                reviewed_at = excluded.reviewed_at
            """,
            (
                record.tenant_id,
                record.id,
                record.provider_id,
                record.operation,
                self._json(record.to_payload().model_dump(mode="json")),
                record.status,
                record.requested_by_user_id,
                record.reviewed_by_user_id,
                self._dt(record.created_at),
                self._dt(record.reviewed_at) if record.reviewed_at is not None else None,
            ),
        )

    def _get_change_request(
        self,
        tenant_id: str,
        request_id: str,
    ) -> ModelProviderChangeRequestRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM model_provider_change_requests
                WHERE tenant_id = ? AND request_id = ?
                """,
                (tenant_id, request_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Model provider change request not found: {request_id}")
        return self._change_request_from_row(row)

    def _require_pending_change_request(
        self,
        request: ModelProviderChangeRequestRecord,
    ) -> None:
        if request.status != "pending":
            raise ValueError(f"Model provider change request is not pending: {request.id}")

    def _change_request_from_row(self, row) -> ModelProviderChangeRequestRecord:
        payload = ModelProviderChangePayload.model_validate(self._loads(row["payload"]))
        reviewed_at = row["reviewed_at"]
        return ModelProviderChangeRequestRecord(
            id=row["request_id"],
            tenant_id=row["tenant_id"],
            provider_id=row["provider_id"],
            operation=row["operation"],
            status=row["status"],
            provider_upsert=payload.provider_upsert,
            api_key_secret_ref_id=payload.api_key_secret_ref_id,
            target_status=payload.status,
            rollback_version=payload.rollback_version,
            requested_by_user_id=row["requested_by_user_id"],
            reviewed_by_user_id=row["reviewed_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            reviewed_at=self._parse_dt(reviewed_at) if reviewed_at is not None else None,
        )

    def _save_record(self, record: ModelProviderRecord) -> None:
        with self._connect() as connection:
            self._save_record_with_connection(connection, record)

    def list_provider_versions(
        self,
        tenant_id: str,
        provider_id: str,
    ) -> list[ModelProviderVersionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_provider_versions
                WHERE tenant_id = ? AND provider_id = ?
                ORDER BY version
                """,
                (tenant_id, provider_id),
            ).fetchall()
        return [self._provider_version_from_row(row) for row in rows]

    def rollback_provider_version(
        self,
        tenant_id: str,
        provider_id: str,
        version: int,
        updated_by_user_id: str | None = None,
    ) -> ModelProviderRecord:
        with self._connect() as connection:
            existing = self._get_provider_required_with_connection(
                connection,
                tenant_id,
                provider_id,
            )
            target = self._get_provider_version_required_with_connection(
                connection,
                tenant_id,
                provider_id,
                version,
            )
            record = ModelProviderRecord(
                tenant_id=tenant_id,
                id=provider_id,
                provider=target.provider,
                status=target.status,
                current_version=self._next_provider_version(
                    connection,
                    tenant_id,
                    provider_id,
                ),
                updated_by_user_id=updated_by_user_id,
                created_at=existing.created_at,
                updated_at=utc_now(),
            )
            self._save_record_with_connection(connection, record)
            self._append_version_with_connection(
                connection,
                record,
                "rollback",
                updated_by_user_id,
            )
        return record

    def _get_provider_optional(
        self,
        tenant_id: str,
        provider_id: str,
    ) -> ModelProviderRecord | None:
        with self._connect() as connection:
            return self._get_provider_optional_with_connection(
                connection,
                tenant_id,
                provider_id,
            )

    def _get_provider_optional_with_connection(
        self,
        connection,
        tenant_id: str,
        provider_id: str,
    ) -> ModelProviderRecord | None:
        row = connection.execute(
            """
            SELECT * FROM model_provider_records
            WHERE tenant_id = ? AND provider_id = ?
            """,
            (tenant_id, provider_id),
        ).fetchone()
        if row is None:
            return None
        return self._provider_record_from_row(row)

    def _get_provider_required_with_connection(
        self,
        connection,
        tenant_id: str,
        provider_id: str,
    ) -> ModelProviderRecord:
        record = self._get_provider_optional_with_connection(
            connection,
            tenant_id,
            provider_id,
        )
        if record is None:
            raise NotFoundError(f"Model provider not found: {provider_id}")
        return record

    def _save_record_with_connection(
        self,
        connection,
        record: ModelProviderRecord,
    ) -> None:
        self._ensure_tenant(connection, record.tenant_id)
        if record.provider.workspace_id is not None:
            self._ensure_workspace(
                connection,
                record.tenant_id,
                record.provider.workspace_id,
            )
        connection.execute(
            """
            INSERT INTO model_provider_records (
                tenant_id, provider_id, config, status, current_version,
                updated_by_user_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, provider_id) DO UPDATE SET
                config = excluded.config,
                status = excluded.status,
                current_version = excluded.current_version,
                updated_by_user_id = excluded.updated_by_user_id,
                updated_at = excluded.updated_at
            """,
            (
                record.tenant_id,
                record.id,
                self._json(record.provider.model_dump(mode="json")),
                record.status,
                record.current_version,
                record.updated_by_user_id,
                self._dt(record.created_at),
                self._dt(record.updated_at),
            ),
        )

    def _append_version_with_connection(
        self,
        connection,
        record: ModelProviderRecord,
        change_type: Literal["upsert", "status", "credential_rotation", "rollback"],
        created_by_user_id: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO model_provider_versions (
                tenant_id, provider_id, version, config, status,
                change_type, created_by_user_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.tenant_id,
                record.id,
                record.current_version,
                self._json(record.provider.model_dump(mode="json")),
                record.status,
                change_type,
                created_by_user_id,
                self._dt(record.updated_at),
            ),
        )

    def _next_provider_version(
        self,
        connection,
        tenant_id: str,
        provider_id: str,
    ) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1 AS next_version
            FROM model_provider_versions
            WHERE tenant_id = ? AND provider_id = ?
            """,
            (tenant_id, provider_id),
        ).fetchone()
        if row is None:
            return 1
        return int(self._row_value(row, "next_version", 1))

    def _get_provider_version_required_with_connection(
        self,
        connection,
        tenant_id: str,
        provider_id: str,
        version: int,
    ) -> ModelProviderVersionRecord:
        row = connection.execute(
            """
            SELECT * FROM model_provider_versions
            WHERE tenant_id = ? AND provider_id = ? AND version = ?
            """,
            (tenant_id, provider_id, version),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Model provider version not found: {provider_id}@{version}")
        return self._provider_version_from_row(row)

    def _provider_record_from_row(self, row) -> ModelProviderRecord:
        provider = ModelProviderConfig.model_validate(self._loads(row["config"]))
        return ModelProviderRecord(
            tenant_id=row["tenant_id"],
            id=row["provider_id"],
            provider=provider,
            status=row["status"],
            current_version=int(self._row_value(row, "current_version", 0)),
            updated_by_user_id=row["updated_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _provider_version_from_row(self, row) -> ModelProviderVersionRecord:
        provider = ModelProviderConfig.model_validate(self._loads(row["config"]))
        return ModelProviderVersionRecord(
            tenant_id=row["tenant_id"],
            provider_id=row["provider_id"],
            version=int(row["version"]),
            provider=provider,
            status=row["status"],
            change_type=row["change_type"],
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _ensure_workspace(self, connection, tenant_id: str, workspace_id: str) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (workspace_id, tenant_id, workspace_id, self._dt(utc_now())),
        )

    def _json(self, value) -> str:
        return json.dumps(value)

    def _loads(self, value: str):
        return json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _row_value(self, row, key: str, default):
        try:
            return row[key]
        except (IndexError, KeyError, TypeError):
            return default


class InMemoryModelPolicyStore(ModelPolicyStore):
    scopes: dict[str, ModelPolicyScopeRecord] = Field(default_factory=dict)
    versions: dict[str, list[ModelPolicyVersionRecord]] = Field(default_factory=dict)
    change_requests: dict[str, ModelPolicyChangeRequestRecord] = Field(
        default_factory=dict
    )

    def upsert_scope(self, request: ModelPolicyScopeUpsert) -> ModelPolicyScopeRecord:
        return self._upsert_scope(
            request=request,
            change_type="upsert_scope",
            change_request_id=None,
        )

    def _upsert_scope(
        self,
        request: ModelPolicyScopeUpsert,
        change_type: Literal["upsert_scope", "approved_change_request"],
        change_request_id: str | None,
    ) -> ModelPolicyScopeRecord:
        request.to_policy_scope()
        now = utc_now()
        existing = self.scopes.get(self._key(request.tenant_id, request.workspace_id))
        record = ModelPolicyScopeRecord(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            default_model=request.default_model,
            allowed_models=list(request.allowed_models),
            denied_models=list(request.denied_models),
            model_sensitivity_limits=dict(request.model_sensitivity_limits),
            updated_by_user_id=request.updated_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.scopes[self._key(record.tenant_id, record.workspace_id)] = record
        self._append_version(record, change_type, change_request_id)
        return record

    def list_scopes(self, tenant_id: str) -> list[ModelPolicyScopeRecord]:
        return self._sort_scopes(
            [scope for scope in self.scopes.values() if scope.tenant_id == tenant_id]
        )

    def list_all_scopes(self) -> list[ModelPolicyScopeRecord]:
        return self._sort_scopes(list(self.scopes.values()))

    def list_policy_versions(
        self,
        tenant_id: str,
        workspace_id: str | None | object = POLICY_VERSION_ALL_SCOPES,
    ) -> list[ModelPolicyVersionRecord]:
        records: list[ModelPolicyVersionRecord] = []
        if workspace_id is POLICY_VERSION_ALL_SCOPES:
            for key, entries in self.versions.items():
                if key.startswith(f"{tenant_id}:"):
                    records.extend(entries)
        else:
            records = list(self.versions.get(self._key(tenant_id, workspace_id), []))
        return sorted(
            records,
            key=lambda record: (
                record.workspace_id is not None,
                record.workspace_id or "",
                record.version,
            ),
        )

    def create_policy_change_request(
        self,
        request: ModelPolicyChangeRequestCreate,
    ) -> ModelPolicyChangeRequestRecord:
        request.scope_upsert.to_policy_scope()
        record = ModelPolicyChangeRequestRecord(
            tenant_id=request.tenant_id,
            operation=request.operation,
            scope_upsert=request.scope_upsert,
            requested_by_user_id=request.requested_by_user_id,
        )
        self.change_requests[self._change_key(record.tenant_id, record.id)] = record
        return record

    def list_policy_change_requests(
        self,
        tenant_id: str,
    ) -> list[ModelPolicyChangeRequestRecord]:
        return sorted(
            [
                record
                for record in self.change_requests.values()
                if record.tenant_id == tenant_id
            ],
            key=lambda record: (record.created_at, record.id),
        )

    def approve_policy_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelPolicyChangeApplyResult:
        request = self._get_change_request(tenant_id, request_id)
        self._require_pending_change_request(request)
        scope_record = self._upsert_scope(
            request=request.scope_upsert.model_copy(
                update={"updated_by_user_id": reviewed_by_user_id}
            ),
            change_type="approved_change_request",
            change_request_id=request.id,
        )
        reviewed = request.model_copy(
            update={
                "status": "approved",
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self.change_requests[self._change_key(tenant_id, request_id)] = reviewed
        return ModelPolicyChangeApplyResult(
            change_request=reviewed,
            scope_record=scope_record,
        )

    def reject_policy_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelPolicyChangeRequestRecord:
        request = self._get_change_request(tenant_id, request_id)
        self._require_pending_change_request(request)
        reviewed = request.model_copy(
            update={
                "status": "rejected",
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self.change_requests[self._change_key(tenant_id, request_id)] = reviewed
        return reviewed

    def _get_change_request(
        self,
        tenant_id: str,
        request_id: str,
    ) -> ModelPolicyChangeRequestRecord:
        record = self.change_requests.get(self._change_key(tenant_id, request_id))
        if record is None:
            raise NotFoundError(f"Model policy change request not found: {request_id}")
        return record

    def _require_pending_change_request(
        self,
        request: ModelPolicyChangeRequestRecord,
    ) -> None:
        if request.status != "pending":
            raise ValueError(f"Model policy change request is not pending: {request.id}")

    def _key(self, tenant_id: str, workspace_id: str | None) -> str:
        return f"{tenant_id}:{workspace_id or ''}"

    def _change_key(self, tenant_id: str, request_id: str) -> str:
        return f"{tenant_id}:{request_id}"

    def _append_version(
        self,
        record: ModelPolicyScopeRecord,
        change_type: Literal["upsert_scope", "approved_change_request"],
        change_request_id: str | None,
    ) -> None:
        key = self._key(record.tenant_id, record.workspace_id)
        entries = self.versions.setdefault(key, [])
        next_version = max([entry.version for entry in entries], default=0) + 1
        entries.append(
            ModelPolicyVersionRecord(
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                version=next_version,
                default_model=record.default_model,
                allowed_models=list(record.allowed_models),
                denied_models=list(record.denied_models),
                model_sensitivity_limits=dict(record.model_sensitivity_limits),
                change_type=change_type,
                change_request_id=change_request_id,
                created_by_user_id=record.updated_by_user_id,
                created_at=record.updated_at,
            )
        )

    def _sort_scopes(
        self,
        scopes: list[ModelPolicyScopeRecord],
    ) -> list[ModelPolicyScopeRecord]:
        return sorted(
            scopes,
            key=lambda scope: (
                scope.tenant_id,
                scope.workspace_id is not None,
                scope.workspace_id or "",
            ),
        )


class SqlModelPolicyStore(ModelPolicyStore):
    config: DatabaseConfig

    def upsert_scope(self, request: ModelPolicyScopeUpsert) -> ModelPolicyScopeRecord:
        return self._upsert_scope(
            request=request,
            change_type="upsert_scope",
            change_request_id=None,
        )

    def _upsert_scope(
        self,
        request: ModelPolicyScopeUpsert,
        change_type: Literal["upsert_scope", "approved_change_request"],
        change_request_id: str | None,
    ) -> ModelPolicyScopeRecord:
        request.to_policy_scope()
        now = utc_now()
        existing = self._get_scope_optional(request.tenant_id, request.workspace_id)
        record = ModelPolicyScopeRecord(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            default_model=request.default_model,
            allowed_models=list(request.allowed_models),
            denied_models=list(request.denied_models),
            model_sensitivity_limits=dict(request.model_sensitivity_limits),
            updated_by_user_id=request.updated_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, record.tenant_id)
            if record.workspace_id is not None:
                self._ensure_workspace(connection, record.tenant_id, record.workspace_id)
            connection.execute(
                """
                INSERT INTO model_policy_scopes (
                    tenant_id, workspace_id, default_model, allowed_models, denied_models,
                    model_sensitivity_limits,
                    updated_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, workspace_id) DO UPDATE SET
                    default_model = excluded.default_model,
                    allowed_models = excluded.allowed_models,
                    denied_models = excluded.denied_models,
                    model_sensitivity_limits = excluded.model_sensitivity_limits,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    record.tenant_id,
                    self._db_workspace_id(record.workspace_id),
                    record.default_model,
                    self._json(record.allowed_models),
                    self._json(record.denied_models),
                    self._json(record.model_sensitivity_limits),
                    record.updated_by_user_id,
                    self._dt(record.created_at),
                    self._dt(record.updated_at),
                ),
            )
            self._append_version_with_connection(
                connection,
                record,
                change_type,
                change_request_id,
            )
        return record

    def list_scopes(self, tenant_id: str) -> list[ModelPolicyScopeRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_policy_scopes
                WHERE tenant_id = ?
                ORDER BY workspace_id, updated_at
                """,
                (tenant_id,),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_all_scopes(self) -> list[ModelPolicyScopeRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_policy_scopes
                ORDER BY tenant_id, workspace_id, updated_at
                """
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_policy_versions(
        self,
        tenant_id: str,
        workspace_id: str | None | object = POLICY_VERSION_ALL_SCOPES,
    ) -> list[ModelPolicyVersionRecord]:
        with self._connect() as connection:
            if workspace_id is POLICY_VERSION_ALL_SCOPES:
                rows = connection.execute(
                    """
                    SELECT * FROM model_policy_versions
                    WHERE tenant_id = ?
                    ORDER BY workspace_id, version
                    """,
                    (tenant_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM model_policy_versions
                    WHERE tenant_id = ? AND workspace_id = ?
                    ORDER BY version
                    """,
                    (tenant_id, self._db_workspace_id(workspace_id)),
                ).fetchall()
        return [self._version_from_row(row) for row in rows]

    def create_policy_change_request(
        self,
        request: ModelPolicyChangeRequestCreate,
    ) -> ModelPolicyChangeRequestRecord:
        request.scope_upsert.to_policy_scope()
        record = ModelPolicyChangeRequestRecord(
            tenant_id=request.tenant_id,
            operation=request.operation,
            scope_upsert=request.scope_upsert,
            requested_by_user_id=request.requested_by_user_id,
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, record.tenant_id)
            if record.scope_upsert.workspace_id is not None:
                self._ensure_workspace(
                    connection,
                    record.tenant_id,
                    record.scope_upsert.workspace_id,
                )
            self._save_change_request_with_connection(connection, record)
        return record

    def list_policy_change_requests(
        self,
        tenant_id: str,
    ) -> list[ModelPolicyChangeRequestRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_policy_change_requests
                WHERE tenant_id = ?
                ORDER BY created_at, request_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._change_request_from_row(row) for row in rows]

    def approve_policy_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelPolicyChangeApplyResult:
        request = self._get_change_request(tenant_id, request_id)
        self._require_pending_change_request(request)
        scope_record = self._upsert_scope(
            request=request.scope_upsert.model_copy(
                update={"updated_by_user_id": reviewed_by_user_id}
            ),
            change_type="approved_change_request",
            change_request_id=request.id,
        )
        reviewed = request.model_copy(
            update={
                "status": "approved",
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self._save_change_request(reviewed)
        return ModelPolicyChangeApplyResult(
            change_request=reviewed,
            scope_record=scope_record,
        )

    def reject_policy_change_request(
        self,
        tenant_id: str,
        request_id: str,
        reviewed_by_user_id: str,
    ) -> ModelPolicyChangeRequestRecord:
        request = self._get_change_request(tenant_id, request_id)
        self._require_pending_change_request(request)
        reviewed = request.model_copy(
            update={
                "status": "rejected",
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self._save_change_request(reviewed)
        return reviewed

    def _save_change_request(
        self,
        record: ModelPolicyChangeRequestRecord,
    ) -> None:
        with self._connect() as connection:
            self._save_change_request_with_connection(connection, record)

    def _save_change_request_with_connection(
        self,
        connection,
        record: ModelPolicyChangeRequestRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO model_policy_change_requests (
                tenant_id, request_id, operation, payload, status,
                requested_by_user_id, reviewed_by_user_id, created_at, reviewed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, request_id) DO UPDATE SET
                payload = excluded.payload,
                status = excluded.status,
                reviewed_by_user_id = excluded.reviewed_by_user_id,
                reviewed_at = excluded.reviewed_at
            """,
            (
                record.tenant_id,
                record.id,
                record.operation,
                self._json(record.to_payload().model_dump(mode="json")),
                record.status,
                record.requested_by_user_id,
                record.reviewed_by_user_id,
                self._dt(record.created_at),
                self._dt(record.reviewed_at) if record.reviewed_at is not None else None,
            ),
        )

    def _get_change_request(
        self,
        tenant_id: str,
        request_id: str,
    ) -> ModelPolicyChangeRequestRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM model_policy_change_requests
                WHERE tenant_id = ? AND request_id = ?
                """,
                (tenant_id, request_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Model policy change request not found: {request_id}")
        return self._change_request_from_row(row)

    def _require_pending_change_request(
        self,
        request: ModelPolicyChangeRequestRecord,
    ) -> None:
        if request.status != "pending":
            raise ValueError(f"Model policy change request is not pending: {request.id}")

    def _change_request_from_row(self, row) -> ModelPolicyChangeRequestRecord:
        payload = ModelPolicyChangePayload.model_validate(json.loads(row["payload"]))
        reviewed_at = row["reviewed_at"]
        return ModelPolicyChangeRequestRecord(
            id=row["request_id"],
            tenant_id=row["tenant_id"],
            operation=row["operation"],
            status=row["status"],
            scope_upsert=payload.scope_upsert,
            requested_by_user_id=row["requested_by_user_id"],
            reviewed_by_user_id=row["reviewed_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            reviewed_at=self._parse_dt(reviewed_at) if reviewed_at is not None else None,
        )

    def _get_scope_optional(
        self,
        tenant_id: str,
        workspace_id: str | None,
    ) -> ModelPolicyScopeRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM model_policy_scopes
                WHERE tenant_id = ? AND workspace_id = ?
                """,
                (tenant_id, self._db_workspace_id(workspace_id)),
            ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def _append_version_with_connection(
        self,
        connection,
        record: ModelPolicyScopeRecord,
        change_type: Literal["upsert_scope", "approved_change_request"],
        change_request_id: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO model_policy_versions (
                tenant_id, workspace_id, version, default_model,
                allowed_models, denied_models, model_sensitivity_limits,
                change_type, change_request_id, created_by_user_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.tenant_id,
                self._db_workspace_id(record.workspace_id),
                self._next_policy_version(
                    connection,
                    record.tenant_id,
                    record.workspace_id,
                ),
                record.default_model,
                self._json(record.allowed_models),
                self._json(record.denied_models),
                self._json(record.model_sensitivity_limits),
                change_type,
                change_request_id,
                record.updated_by_user_id,
                self._dt(record.updated_at),
            ),
        )

    def _next_policy_version(
        self,
        connection,
        tenant_id: str,
        workspace_id: str | None,
    ) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1 AS next_version
            FROM model_policy_versions
            WHERE tenant_id = ? AND workspace_id = ?
            """,
            (tenant_id, self._db_workspace_id(workspace_id)),
        ).fetchone()
        if row is None:
            return 1
        try:
            return int(row["next_version"])
        except (IndexError, KeyError, TypeError):
            return 1

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _ensure_workspace(self, connection, tenant_id: str, workspace_id: str) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (workspace_id, tenant_id, workspace_id, self._dt(utc_now())),
        )

    def _record_from_row(self, row) -> ModelPolicyScopeRecord:
        return ModelPolicyScopeRecord(
            tenant_id=row["tenant_id"],
            workspace_id=self._model_workspace_id(row["workspace_id"]),
            default_model=row["default_model"],
            allowed_models=self._loads(row["allowed_models"]),
            denied_models=self._loads(row["denied_models"]),
            model_sensitivity_limits=self._loads_dict_int(row["model_sensitivity_limits"]),
            updated_by_user_id=row["updated_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _version_from_row(self, row) -> ModelPolicyVersionRecord:
        return ModelPolicyVersionRecord(
            tenant_id=row["tenant_id"],
            workspace_id=self._model_workspace_id(row["workspace_id"]),
            version=int(row["version"]),
            default_model=row["default_model"],
            allowed_models=self._loads(row["allowed_models"]),
            denied_models=self._loads(row["denied_models"]),
            model_sensitivity_limits=self._loads_dict_int(row["model_sensitivity_limits"]),
            change_type=row["change_type"],
            change_request_id=row["change_request_id"],
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _db_workspace_id(self, workspace_id: str | None) -> str:
        return workspace_id or ""

    def _model_workspace_id(self, workspace_id: str) -> str | None:
        if workspace_id == "":
            return None
        return workspace_id

    def _json(self, value) -> str:
        return json.dumps(value)

    def _loads(self, value: str) -> list[str]:
        loaded = json.loads(value)
        if not isinstance(loaded, list):
            return []
        return [str(item) for item in loaded]

    def _loads_dict_int(self, value: str) -> dict[str, int]:
        loaded = json.loads(value)
        if not isinstance(loaded, dict):
            return {}
        return {str(key): int(item) for key, item in loaded.items()}

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)
