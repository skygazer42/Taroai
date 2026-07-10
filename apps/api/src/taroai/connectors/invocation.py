from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.connectors.models import (
    ConnectorCapability,
    ConnectorDefinition,
    ConnectorStatus,
)
from taroai.connectors.service import ConnectorAccessDeniedError
from taroai.tool_gateway import (
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchemaValidationError,
)
from taroai.tool_gateway.schema import JsonSchemaValidator


CONNECTOR_INVOCATION_METER = "connector_invocation_count"


class ConnectorInvocationStatus(str, Enum):
    READY = "ready"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


class ConnectorInvocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    capability_name: str = Field(min_length=1)
    tool_input: dict[str, Any] = Field(default_factory=dict)
    granted_scopes: list[str] = Field(default_factory=list)
    approved: bool = False
    approval_id: str | None = Field(default=None, min_length=1)

    def to_invocation_request(
        self,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        connector_id: str,
    ) -> "ConnectorInvocationRequest":
        return ConnectorInvocationRequest(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=self.run_id,
            step_id=self.step_id,
            connector_id=connector_id,
            capability_name=self.capability_name,
            tool_input=self.tool_input,
            granted_scopes=self.granted_scopes,
            approved=self.approved,
            approval_id=self.approval_id,
        )


class ConnectorInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    connector_id: str = Field(min_length=1)
    capability_name: str = Field(min_length=1)
    tool_input: dict[str, Any] = Field(default_factory=dict)
    granted_scopes: list[str] = Field(default_factory=list)
    approved: bool = False
    approval_id: str | None = Field(default=None, min_length=1)


class ConnectorInvocationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    connector_id: str = Field(min_length=1)
    capability_name: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ConnectorInvocationStatus
    required_scopes: list[str] = Field(default_factory=list)
    granted_scopes: list[str] = Field(default_factory=list)
    missing_scopes: list[str] = Field(default_factory=list)
    risk_level: str | None = None
    approval_required: bool = False
    approved: bool = False
    approval_id: str | None = None
    input_keys: list[str] = Field(default_factory=list)
    reason: str | None = None
    billing_meter_type: str | None = None


class ConnectorInvocationService(BaseModel):
    def evaluate(
        self,
        connector: ConnectorDefinition,
        request: ConnectorInvocationRequest,
    ) -> ConnectorInvocationDecision:
        self._assert_connector_scope(connector, request)
        capability = self._find_capability(connector, request.capability_name)
        tool_name = self._tool_name(connector.id, request.capability_name)
        input_keys = list(request.tool_input.keys())
        if capability is None:
            return self._decision(
                request=request,
                tool_name=tool_name,
                status=ConnectorInvocationStatus.DENIED,
                input_keys=input_keys,
                reason=f"connector capability is not available: {request.capability_name}",
            )

        policy = self._build_policy(connector, capability, tool_name)
        gateway_request = ToolGatewayRequest(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            run_id=request.run_id,
            step_id=request.step_id,
            tool_name=tool_name,
            tool_input=request.tool_input,
            granted_scopes=request.granted_scopes,
            approved=request.approved,
        )
        policy_decision = ToolGateway(policies={tool_name: policy}).check_policy(
            gateway_request
        )
        if not policy_decision.allowed:
            return self._decision(
                request=request,
                tool_name=tool_name,
                status=ConnectorInvocationStatus.DENIED,
                capability=capability,
                input_keys=input_keys,
                missing_scopes=policy_decision.missing_scopes,
                reason=policy_decision.reason,
            )
        if policy_decision.approval_required and not request.approved:
            return self._decision(
                request=request,
                tool_name=tool_name,
                status=ConnectorInvocationStatus.APPROVAL_REQUIRED,
                capability=capability,
                input_keys=input_keys,
                approval_required=True,
            )

        self._validate_input(request.tool_input, capability.input_schema)
        return self._decision(
            request=request,
            tool_name=tool_name,
            status=ConnectorInvocationStatus.READY,
            capability=capability,
            input_keys=input_keys,
            approval_required=capability.approval_required,
            billing_meter_type=CONNECTOR_INVOCATION_METER,
        )

    def _assert_connector_scope(
        self,
        connector: ConnectorDefinition,
        request: ConnectorInvocationRequest,
    ) -> None:
        if connector.id != request.connector_id:
            raise ConnectorAccessDeniedError("connector id does not match invocation")
        if connector.tenant_id != request.tenant_id:
            raise ConnectorAccessDeniedError("connector is not in tenant")
        if connector.workspace_id != request.workspace_id:
            raise ConnectorAccessDeniedError("connector is not in workspace")

    def _find_capability(
        self,
        connector: ConnectorDefinition,
        capability_name: str,
    ) -> ConnectorCapability | None:
        for capability in connector.capabilities:
            if capability.name == capability_name:
                return capability
        return None

    def _build_policy(
        self,
        connector: ConnectorDefinition,
        capability: ConnectorCapability,
        tool_name: str,
    ) -> ToolPolicy:
        return ToolPolicy(
            tool_name=tool_name,
            required_scopes=capability.required_scopes,
            risk_level=ToolRiskLevel(capability.risk_level),
            approval_required=capability.approval_required,
            enabled=connector.status == ConnectorStatus.ENABLED and capability.enabled,
            input_schema=capability.input_schema,
            output_schema=capability.output_schema,
        )

    def _validate_input(self, tool_input: dict[str, Any], schema: dict[str, Any]) -> None:
        result = JsonSchemaValidator(json_schema=schema).validate(tool_input)
        if result.valid:
            return
        details = "; ".join(result.errors)
        raise ToolSchemaValidationError(f"connector input is invalid: {details}")

    def _decision(
        self,
        request: ConnectorInvocationRequest,
        tool_name: str,
        status: ConnectorInvocationStatus,
        input_keys: list[str],
        capability: ConnectorCapability | None = None,
        missing_scopes: list[str] | None = None,
        approval_required: bool = False,
        reason: str | None = None,
        billing_meter_type: str | None = None,
    ) -> ConnectorInvocationDecision:
        required_scopes: list[str] = []
        risk_level = None
        if capability is not None:
            required_scopes = capability.required_scopes
            risk_level = capability.risk_level
        return ConnectorInvocationDecision(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            run_id=request.run_id,
            step_id=request.step_id,
            connector_id=request.connector_id,
            capability_name=request.capability_name,
            tool_name=tool_name,
            status=status,
            required_scopes=required_scopes,
            granted_scopes=request.granted_scopes,
            missing_scopes=missing_scopes or [],
            risk_level=risk_level,
            approval_required=approval_required,
            approved=request.approved,
            approval_id=request.approval_id,
            input_keys=input_keys,
            reason=reason,
            billing_meter_type=billing_meter_type,
        )

    def _tool_name(self, connector_id: str, capability_name: str) -> str:
        return f"connector.{connector_id}.{capability_name}"
