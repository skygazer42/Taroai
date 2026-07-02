from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from taroai.auth import AuthInvalidCredentialsError, AuthRequiredError
from taroai.memory import MemoryWriteRejectedError
from taroai.model_gateway import (
    ModelGatewayConfigurationError,
    ModelGatewayResponseError,
    ModelPolicyDeniedError,
)
from taroai.sandbox import (
    BrowserProviderUnavailableError,
    SandboxExecutionError,
    SandboxProviderUnavailableError,
)
from taroai.lifecycle import TenantOffboardingTransitionError
from taroai.storage import ObjectStorageConfigurationError, StorageContentRejectedError
from taroai.store import NotFoundError, TenantAccessError
from taroai.tool_gateway import ToolApprovalRequiredError, ToolExecutionError
from taroai.workers import RedisQueueConfigurationError


class ApiError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ApiExceptionRule(BaseModel):
    exception_type: type[Exception]
    status_code: int
    code: str
    message: str | None = None
    retryable: bool = False
    expose_message: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def matches(self, error: Exception) -> bool:
        return isinstance(error, self.exception_type)

    def to_api_error(self, error: Exception, expose_internal_errors: bool) -> ApiError:
        message = self.message
        if self.expose_message or (expose_internal_errors and message is None):
            message = str(error)
        return ApiError(
            code=self.code,
            message=message or "internal server error",
            retryable=self.retryable,
        )


def default_exception_rules() -> list[ApiExceptionRule]:
    return [
        ApiExceptionRule(
            exception_type=AuthRequiredError,
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="auth_required",
            message="authentication required",
        ),
        ApiExceptionRule(
            exception_type=AuthInvalidCredentialsError,
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="invalid_credentials",
            message="invalid credentials",
        ),
        ApiExceptionRule(
            exception_type=TenantAccessError,
            status_code=status.HTTP_403_FORBIDDEN,
            code="tenant_access_denied",
            message="tenant access denied",
        ),
        ApiExceptionRule(
            exception_type=NotFoundError,
            status_code=status.HTTP_404_NOT_FOUND,
            code="not_found",
            message="not found",
        ),
        ApiExceptionRule(
            exception_type=ModelGatewayConfigurationError,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="model_gateway_unavailable",
            expose_message=True,
            retryable=True,
        ),
        ApiExceptionRule(
            exception_type=ModelGatewayResponseError,
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="model_gateway_error",
            expose_message=True,
            retryable=True,
        ),
        ApiExceptionRule(
            exception_type=ModelPolicyDeniedError,
            status_code=status.HTTP_403_FORBIDDEN,
            code="model_policy_denied",
            expose_message=True,
        ),
        ApiExceptionRule(
            exception_type=MemoryWriteRejectedError,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="memory_write_rejected",
            message="memory write rejected",
        ),
        ApiExceptionRule(
            exception_type=ToolApprovalRequiredError,
            status_code=status.HTTP_409_CONFLICT,
            code="tool_approval_required",
            expose_message=True,
        ),
        ApiExceptionRule(
            exception_type=ToolExecutionError,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="tool_execution_failed",
            expose_message=True,
        ),
        ApiExceptionRule(
            exception_type=BrowserProviderUnavailableError,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="browser_provider_unavailable",
            expose_message=True,
            retryable=True,
        ),
        ApiExceptionRule(
            exception_type=SandboxProviderUnavailableError,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="sandbox_provider_unavailable",
            expose_message=True,
            retryable=True,
        ),
        ApiExceptionRule(
            exception_type=SandboxExecutionError,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="sandbox_execution_failed",
            expose_message=True,
        ),
        ApiExceptionRule(
            exception_type=RedisQueueConfigurationError,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="job_queue_unavailable",
            expose_message=True,
            retryable=True,
        ),
        ApiExceptionRule(
            exception_type=ObjectStorageConfigurationError,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="object_storage_unavailable",
            expose_message=True,
            retryable=True,
        ),
        ApiExceptionRule(
            exception_type=StorageContentRejectedError,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="storage_content_rejected",
            message="storage content rejected",
        ),
        ApiExceptionRule(
            exception_type=TenantOffboardingTransitionError,
            status_code=status.HTTP_409_CONFLICT,
            code="tenant_offboarding_transition_invalid",
            expose_message=True,
        ),
        ApiExceptionRule(
            exception_type=ValueError,
            status_code=status.HTTP_409_CONFLICT,
            code="conflict",
            expose_message=True,
        ),
        ApiExceptionRule(
            exception_type=Exception,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_server_error",
            retryable=True,
        ),
    ]


class ApiExceptionManager(BaseModel):
    expose_internal_errors: bool = False
    rules: list[ApiExceptionRule] = Field(default_factory=default_exception_rules)

    def register(self, app: FastAPI) -> None:
        for exception_type in self.handled_exception_types():
            app.add_exception_handler(exception_type, self.handle_exception)

    def add_rule(self, rule: ApiExceptionRule) -> None:
        self.rules = [
            existing
            for existing in self.rules
            if existing.exception_type is not rule.exception_type
        ]
        catch_all_index = self.catch_all_index()
        self.rules.insert(catch_all_index, rule)

    def handled_exception_types(self) -> tuple[type[Exception], ...]:
        return tuple(rule.exception_type for rule in self.rules)

    async def handle_exception(self, request: Request, error: Exception) -> JSONResponse:
        return self.to_response(error)

    def to_response(self, error: Exception) -> JSONResponse:
        rule = self.rule_for(error)
        return JSONResponse(
            status_code=rule.status_code,
            content=rule.to_api_error(error, self.expose_internal_errors).model_dump(mode="json"),
        )

    def rule_for(self, error: Exception) -> ApiExceptionRule:
        for rule in self.rules:
            if rule.matches(error):
                return rule
        return ApiExceptionRule(
            exception_type=Exception,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_server_error",
            retryable=True,
        )

    def catch_all_index(self) -> int:
        for index, rule in enumerate(self.rules):
            if rule.exception_type is Exception:
                return index
        return len(self.rules)
