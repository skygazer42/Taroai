import json
import re
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from taroai.audit import AuditActor, AuditEventCreate
from taroai.guardrails.models import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationRequest,
    GuardrailRedaction,
    GuardrailStage,
)
from taroai.secrets import SecretAccessDeniedError, SecretNotFoundError, SecretService
from taroai.tool_gateway.models import (
    ToolAuditRecord,
    ToolAuditRecorder,
    ToolGatewayRequest,
    ToolHandler,
    ToolPolicy,
    ToolPolicyDecision,
    ToolResult,
)
from taroai.tool_gateway.schema import JsonSchemaValidator


class ToolExecutionError(RuntimeError):
    pass


class ToolApprovalRequiredError(ToolExecutionError):
    pass


class ToolSchemaValidationError(ToolExecutionError):
    pass


class ToolGateway(BaseModel):
    policies: dict[str, ToolPolicy] = Field(default_factory=dict)
    audit_recorder: ToolAuditRecorder | None = None
    audit_service: Any | None = None
    secret_service: SecretService | None = None
    guardrail_service: Any | None = None
    _handlers: dict[str, ToolHandler] = PrivateAttr(default_factory=dict)

    def register_tool(self, policy: ToolPolicy, handler: ToolHandler) -> ToolPolicy:
        self.policies[policy.tool_name] = policy
        self._handlers[policy.tool_name] = handler
        return policy

    def can_execute_tool(self, tool_name: str) -> bool:
        return tool_name in self.policies and tool_name in self._handlers

    def execute_for_run(
        self,
        state,
        step,
        granted_scopes: list[str] | None = None,
        thread_id: str | None = None,
    ) -> ToolResult:
        return self.execute_request(
            ToolGatewayRequest(
                tenant_id=state.tenant_id,
                workspace_id=state.workspace_id,
                user_id=state.user_id,
                run_id=state.run_id,
                thread_id=thread_id,
                step_id=step.id,
                tool_name=step.tool_name,
                skill_id=step.skill_id,
                tool_input=step.tool_input,
                granted_scopes=granted_scopes or [],
                approved=step.id in state.approved_step_ids,
            )
        )

    def execute_request(self, request: ToolGatewayRequest) -> ToolResult:
        decision = self.check_policy(request)
        if not decision.allowed:
            self._record_audit("tool.blocked", request, decision)
            raise ToolExecutionError(
                decision.reason or f"Tool is not permitted: {request.tool_name}"
            )
        if decision.approval_required and not request.approved:
            self._record_audit("tool.approval_required", request, decision)
            raise ToolApprovalRequiredError(
                f"Tool approval required: {request.tool_name}"
            )

        request = self._apply_guardrails(request)
        handler = self._handlers.get(request.tool_name)
        if handler is None:
            raise ToolExecutionError(
                f"Tool handler is not registered: {request.tool_name}"
            )

        policy = self.policies[request.tool_name]
        request = self._with_secret_leases(request, policy)
        self._validate_payload("input", request.tool_input, policy.input_schema)
        result = handler(request)
        if isinstance(result, ToolResult):
            tool_result = result
        else:
            tool_result = ToolResult.model_validate(result)
        self._validate_payload("output", tool_result.output, policy.output_schema)
        return tool_result

    def check_policy(self, request: ToolGatewayRequest) -> ToolPolicyDecision:
        policy = self.policies.get(request.tool_name)
        if policy is None:
            return ToolPolicyDecision(
                allowed=False,
                reason=f"Tool is not registered: {request.tool_name}",
            )
        if not policy.enabled:
            return ToolPolicyDecision(
                allowed=False,
                reason=f"Tool is disabled: {request.tool_name}",
            )

        missing_scopes = sorted(
            set(policy.required_scopes) - set(request.granted_scopes)
        )
        if missing_scopes:
            return ToolPolicyDecision(
                allowed=False,
                reason=f"Tool is not permitted: missing scopes: {', '.join(missing_scopes)}",
                missing_scopes=missing_scopes,
            )

        return ToolPolicyDecision(
            allowed=True,
            approval_required=policy.approval_required,
        )

    def execute(self, step) -> ToolResult:
        raise ToolExecutionError(f"Tool is not registered: {step.tool_name}")

    def _apply_guardrails(self, request: ToolGatewayRequest) -> ToolGatewayRequest:
        if self.guardrail_service is None:
            return request
        decision = self.guardrail_service.evaluate(
            GuardrailEvaluationRequest(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                stage=GuardrailStage.TOOL_REQUEST,
                content=self._tool_input_content(request.tool_input),
                attributes=self._tool_input_attributes(request),
            )
        )
        if decision.blocked:
            self._record_guardrail_audit("tool.guardrail_blocked", request, decision)
            raise ToolExecutionError(
                decision.message or f"Tool is blocked by guardrail: {request.tool_name}"
            )
        if decision.approval_required and not request.approved:
            self._record_guardrail_audit(
                "tool.guardrail_approval_required", request, decision
            )
            raise ToolApprovalRequiredError(
                decision.message or f"Tool approval required: {request.tool_name}"
            )
        if decision.action == GuardrailAction.REDACT and decision.redactions:
            return request.model_copy(
                update={
                    "tool_input": self._apply_guardrail_redactions(
                        request.tool_input,
                        decision.redactions,
                    )
                }
            )
        return request

    def _tool_input_content(self, tool_input: dict[str, Any]) -> str:
        return json.dumps(tool_input, sort_keys=True, default=str)

    def _tool_input_attributes(self, request: ToolGatewayRequest) -> dict[str, Any]:
        attributes = dict(request.tool_input)
        attributes.update(
            {
                "tool_name": request.tool_name,
                "skill_id": request.skill_id,
                "tenant_id": request.tenant_id,
                "workspace_id": request.workspace_id,
                "user_id": request.user_id,
                "run_id": request.run_id,
                "step_id": request.step_id,
            }
        )
        return attributes

    def _apply_guardrail_redactions(
        self,
        value,
        redactions: list[GuardrailRedaction],
    ):
        if isinstance(value, dict):
            return {
                key: self._apply_guardrail_redactions(item, redactions)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._apply_guardrail_redactions(item, redactions) for item in value
            ]
        if isinstance(value, str):
            redacted = value
            for redaction in redactions:
                flags = 0 if redaction.case_sensitive else re.IGNORECASE
                redacted = re.sub(
                    re.escape(redaction.text),
                    redaction.replacement,
                    redacted,
                    flags=flags,
                )
            return redacted
        return value

    def _with_secret_leases(
        self,
        request: ToolGatewayRequest,
        policy: ToolPolicy,
    ) -> ToolGatewayRequest:
        if not policy.secret_requirements:
            return request
        if self.secret_service is None:
            raise ToolExecutionError("secret service is not configured")
        try:
            leases = [
                self.secret_service.create_lease(
                    tenant_id=request.tenant_id,
                    workspace_id=request.workspace_id,
                    secret_id=requirement.secret_id,
                    tool_name=request.tool_name,
                    actions=requirement.actions,
                    ttl_seconds=requirement.ttl_seconds,
                    run_id=request.run_id,
                    step_id=request.step_id,
                    session_id=self._lease_session_id(request),
                )
                for requirement in policy.secret_requirements
            ]
        except (SecretAccessDeniedError, SecretNotFoundError) as error:
            raise ToolExecutionError(str(error)) from error
        return request.model_copy(update={"secret_leases": leases})

    def _lease_session_id(self, request: ToolGatewayRequest) -> str | None:
        session_id = request.tool_input.get("session_id")
        if session_id is None:
            return None
        return str(session_id)

    def _record_audit(
        self,
        event_type: str,
        request: ToolGatewayRequest,
        decision: ToolPolicyDecision,
    ) -> None:
        policy = self.policies.get(request.tool_name)
        record = ToolAuditRecord(
            event_type=event_type,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            run_id=request.run_id,
            step_id=request.step_id,
            tool_name=request.tool_name,
            skill_id=request.skill_id,
            reason=decision.reason,
            missing_scopes=decision.missing_scopes,
            risk_level=policy.risk_level if policy is not None else None,
            granted_scopes=request.granted_scopes,
            approved=request.approved,
            tool_input=self._redact_tool_input(request.tool_input),
        )
        if self.audit_recorder is not None:
            self.audit_recorder(record)
        if self.audit_service is not None:
            self.audit_service.record(
                AuditEventCreate(
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    user_id=record.user_id,
                    run_id=record.run_id,
                    event_type=record.event_type,
                    metadata=record.model_dump(
                        mode="json",
                        exclude={
                            "event_type",
                            "tenant_id",
                            "workspace_id",
                            "user_id",
                            "run_id",
                        },
                    ),
                    actor=AuditActor(
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        actor_type="user" if record.user_id is not None else "system",
                    ),
                )
            )

    def _record_guardrail_audit(
        self,
        event_type: str,
        request: ToolGatewayRequest,
        decision: GuardrailDecision,
    ) -> None:
        policy = self.policies.get(request.tool_name)
        record = ToolAuditRecord(
            event_type=event_type,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            run_id=request.run_id,
            step_id=request.step_id,
            tool_name=request.tool_name,
            skill_id=request.skill_id,
            reason=decision.message,
            risk_level=policy.risk_level if policy is not None else None,
            granted_scopes=request.granted_scopes,
            approved=request.approved,
            tool_input=self._redact_tool_input(request.tool_input),
            guardrail_action=decision.action,
            guardrail_rule_ids=decision.matched_rule_ids,
            guardrail_detector_finding_ids=decision.detector_finding_ids,
        )
        if self.audit_recorder is not None:
            self.audit_recorder(record)
        if self.audit_service is not None:
            self.audit_service.record(
                AuditEventCreate(
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    user_id=record.user_id,
                    run_id=record.run_id,
                    event_type=record.event_type,
                    metadata=record.model_dump(
                        mode="json",
                        exclude={
                            "event_type",
                            "tenant_id",
                            "workspace_id",
                            "user_id",
                            "run_id",
                        },
                    ),
                    actor=AuditActor(
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        actor_type="user" if record.user_id is not None else "system",
                    ),
                )
            )

    def _validate_payload(self, direction: str, payload: dict, schema: dict) -> None:
        result = JsonSchemaValidator(json_schema=schema).validate(payload)
        if not result.valid:
            details = "; ".join(result.errors)
            raise ToolSchemaValidationError(f"tool {direction} is invalid: {details}")

    def _redact_tool_input(self, value):
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if self._is_sensitive_key(key):
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = self._redact_tool_input(item)
            return redacted
        if isinstance(value, list):
            return [self._redact_tool_input(item) for item in value]
        return value

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        if normalized in {
            "secret",
            "token",
            "password",
            "api_key",
            "apikey",
            "credential",
        }:
            return True
        return normalized.endswith(
            (
                "_secret",
                "_token",
                "_password",
                "_api_key",
                "_apikey",
                "_credential",
            )
        )
