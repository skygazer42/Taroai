from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import new_id, utc_now
from taroai.policy import PolicyDecision, PolicyRequest, PolicyService


class QuarantineTargetType(str, Enum):
    RUN = "run"
    AGENT = "agent"
    SKILL = "skill"
    CONNECTOR = "connector"
    TRIGGER = "trigger"
    TENANT = "tenant"


class QuarantineStatus(str, Enum):
    ACTIVE = "active"
    CLEARED = "cleared"


class KillSwitchScope(str, Enum):
    HIGH_RISK_TOOLS = "high_risk_tools"
    EXTERNAL_WRITES = "external_writes"
    MODEL_PROVIDERS = "model_providers"
    SANDBOX_CREATION = "sandbox_creation"


class QuarantineRecord(BaseModel):
    id: str
    tenant_id: str
    target_type: QuarantineTargetType
    target_id: str
    reason_code: str
    created_by_user_id: str
    status: QuarantineStatus = QuarantineStatus.ACTIVE
    audit_event_id: str | None = None
    created_at: datetime
    cleared_at: datetime | None = None
    cleared_by_user_id: str | None = None


class KillSwitchRecord(BaseModel):
    id: str
    tenant_id: str
    scope: KillSwitchScope
    reason_code: str
    enabled_by_user_id: str
    enabled: bool = True
    audit_event_id: str | None = None
    enabled_at: datetime
    disabled_at: datetime | None = None
    disabled_by_user_id: str | None = None


class InMemoryOperationalControlService(BaseModel):
    audit_store: Any | None = Field(default=None, exclude=True, repr=False)
    quarantines: dict[str, QuarantineRecord] = Field(default_factory=dict)
    kill_switches: dict[str, KillSwitchRecord] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def quarantine(
        self,
        tenant_id: str,
        target_type: QuarantineTargetType,
        target_id: str,
        reason_code: str,
        created_by_user_id: str,
    ) -> QuarantineRecord:
        quarantine = QuarantineRecord(
            id=new_id("quarantine"),
            tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
            reason_code=reason_code,
            created_by_user_id=created_by_user_id,
            created_at=utc_now(),
        )
        audit_event = self._record_audit_event(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            event_type="quarantine.enabled",
            metadata={
                "quarantine_id": quarantine.id,
                "target_type": target_type.value,
                "target_id": target_id,
                "reason_code": reason_code,
                "created_by_user_id": created_by_user_id,
            },
        )
        if audit_event is not None:
            quarantine = quarantine.model_copy(
                update={"audit_event_id": audit_event.id}
            )
        self.quarantines[quarantine.id] = quarantine
        return quarantine

    def enable_kill_switch(
        self,
        tenant_id: str,
        scope: KillSwitchScope,
        reason_code: str,
        enabled_by_user_id: str,
    ) -> KillSwitchRecord:
        kill_switch = KillSwitchRecord(
            id=new_id("kill_switch"),
            tenant_id=tenant_id,
            scope=scope,
            reason_code=reason_code,
            enabled_by_user_id=enabled_by_user_id,
            enabled_at=utc_now(),
        )
        audit_event = self._record_audit_event(
            tenant_id=tenant_id,
            user_id=enabled_by_user_id,
            event_type="kill_switch.enabled",
            metadata={
                "kill_switch_id": kill_switch.id,
                "scope": scope.value,
                "reason_code": reason_code,
                "enabled_by_user_id": enabled_by_user_id,
            },
        )
        if audit_event is not None:
            kill_switch = kill_switch.model_copy(
                update={"audit_event_id": audit_event.id}
            )
        self.kill_switches[self._kill_switch_key(tenant_id, scope)] = kill_switch
        return kill_switch

    def find_active_quarantine(
        self,
        tenant_id: str,
        target_type: QuarantineTargetType,
        target_id: str,
    ) -> QuarantineRecord | None:
        for quarantine in self.quarantines.values():
            if (
                quarantine.tenant_id == tenant_id
                and quarantine.target_type == target_type
                and quarantine.target_id == target_id
                and quarantine.status == QuarantineStatus.ACTIVE
            ):
                return quarantine
        return None

    def find_enabled_kill_switch(
        self,
        tenant_id: str,
        scope: KillSwitchScope,
    ) -> KillSwitchRecord | None:
        kill_switch = self.kill_switches.get(self._kill_switch_key(tenant_id, scope))
        if kill_switch is not None and kill_switch.enabled:
            return kill_switch
        return None

    def evaluate_runtime_execution(
        self,
        request: PolicyRequest,
    ) -> PolicyDecision:
        tenant_decision = self._quarantine_decision(
            tenant_id=request.tenant_id,
            target_type=QuarantineTargetType.TENANT,
            target_id=request.tenant_id,
            label="tenant",
        )
        if not tenant_decision.allowed:
            return tenant_decision
        if request.run_id is not None:
            run_decision = self._quarantine_decision(
                tenant_id=request.tenant_id,
                target_type=QuarantineTargetType.RUN,
                target_id=request.run_id,
                label="run",
            )
            if not run_decision.allowed:
                return run_decision
        agent_id = self._context_value(request, "agent_id")
        if agent_id is not None:
            agent_decision = self._quarantine_decision(
                tenant_id=request.tenant_id,
                target_type=QuarantineTargetType.AGENT,
                target_id=agent_id,
                label="agent",
            )
            if not agent_decision.allowed:
                return agent_decision
        return PolicyDecision.allow()

    def evaluate_runtime_step(
        self,
        request: PolicyRequest,
    ) -> PolicyDecision:
        execution_decision = self.evaluate_runtime_execution(request)
        if not execution_decision.allowed:
            return execution_decision
        for target_type, context_key, label in (
            (QuarantineTargetType.SKILL, "skill_id", "skill"),
            (QuarantineTargetType.CONNECTOR, "connector_id", "connector"),
            (QuarantineTargetType.TRIGGER, "trigger_id", "trigger"),
        ):
            target_id = self._context_value(request, context_key)
            if target_id is None:
                continue
            decision = self._quarantine_decision(
                tenant_id=request.tenant_id,
                target_type=target_type,
                target_id=target_id,
                label=label,
            )
            if not decision.allowed:
                return decision
        sandbox_decision = self._sandbox_creation_kill_switch_decision(request)
        if not sandbox_decision.allowed:
            return sandbox_decision
        high_risk_decision = self._high_risk_tools_kill_switch_decision(request)
        if not high_risk_decision.allowed:
            return high_risk_decision
        external_write_decision = self._external_writes_kill_switch_decision(request)
        if not external_write_decision.allowed:
            return external_write_decision
        return PolicyDecision.allow()

    def _sandbox_creation_kill_switch_decision(
        self,
        request: PolicyRequest,
    ) -> PolicyDecision:
        if self._context_value(request, "tool_name") != "sandbox.command":
            return PolicyDecision.allow()
        kill_switch = self.find_enabled_kill_switch(
            request.tenant_id,
            KillSwitchScope.SANDBOX_CREATION,
        )
        if kill_switch is None:
            return PolicyDecision.allow()
        return PolicyDecision.deny(
            reason=(
                "sandbox_creation kill switch is enabled: "
                f"{kill_switch.reason_code}"
            ),
            metadata={
                "target_type": "kill_switch",
                "target_id": KillSwitchScope.SANDBOX_CREATION.value,
            },
        )

    def _high_risk_tools_kill_switch_decision(
        self,
        request: PolicyRequest,
    ) -> PolicyDecision:
        risk_level = request.risk_level or self._context_value(request, "risk_level")
        if risk_level not in {"high", "critical"}:
            return PolicyDecision.allow()
        kill_switch = self.find_enabled_kill_switch(
            request.tenant_id,
            KillSwitchScope.HIGH_RISK_TOOLS,
        )
        if kill_switch is None:
            return PolicyDecision.allow()
        return PolicyDecision.deny(
            reason=(
                "high_risk_tools kill switch is enabled: "
                f"{kill_switch.reason_code}"
            ),
            metadata={
                "target_type": "kill_switch",
                "target_id": KillSwitchScope.HIGH_RISK_TOOLS.value,
            },
        )

    def _external_writes_kill_switch_decision(
        self,
        request: PolicyRequest,
    ) -> PolicyDecision:
        if request.context.get("external_write") is not True:
            return PolicyDecision.allow()
        kill_switch = self.find_enabled_kill_switch(
            request.tenant_id,
            KillSwitchScope.EXTERNAL_WRITES,
        )
        if kill_switch is None:
            return PolicyDecision.allow()
        return PolicyDecision.deny(
            reason=(
                "external_writes kill switch is enabled: "
                f"{kill_switch.reason_code}"
            ),
            metadata={
                "target_type": "kill_switch",
                "target_id": KillSwitchScope.EXTERNAL_WRITES.value,
            },
        )

    def _quarantine_decision(
        self,
        tenant_id: str,
        target_type: QuarantineTargetType,
        target_id: str,
        label: str,
    ) -> PolicyDecision:
        quarantine = self.find_active_quarantine(
            tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
        )
        if quarantine is None:
            return PolicyDecision.allow()
        return PolicyDecision.deny(
            reason=f"{label} is quarantined: {quarantine.reason_code}",
            metadata={
                "target_type": target_type.value,
                "target_id": target_id,
            },
        )

    def _record_audit_event(
        self,
        tenant_id: str,
        user_id: str,
        event_type: str,
        metadata: dict[str, Any],
    ):
        if self.audit_store is None:
            return None
        return self.audit_store.record_audit_event(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=user_id,
            run_id=None,
            event_type=event_type,
            metadata=metadata,
        )

    def _kill_switch_key(self, tenant_id: str, scope: KillSwitchScope) -> str:
        return f"{tenant_id}:{scope.value}"

    def _context_value(self, request: PolicyRequest, key: str) -> str | None:
        value = request.context.get(key)
        if isinstance(value, str) and value.strip():
            return value
        return None


class OperationalPolicyService(PolicyService):
    control_service: InMemoryOperationalControlService
    base_policy_service: PolicyService | None = None

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        if self.base_policy_service is None:
            return PolicyDecision.allow()
        return self.base_policy_service.decide(request)

    def decide_runtime_execution(self, request: PolicyRequest) -> PolicyDecision:
        decision = self.control_service.evaluate_runtime_execution(request)
        if not decision.allowed or self.base_policy_service is None:
            return decision
        return self.base_policy_service.decide_runtime_execution(request)

    def decide_runtime_step(self, request: PolicyRequest) -> PolicyDecision:
        decision = self.control_service.evaluate_runtime_step(request)
        if not decision.allowed or self.base_policy_service is None:
            return decision
        return self.base_policy_service.decide_runtime_step(request)
