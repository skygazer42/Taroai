from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import utc_now
from taroai.store import NotFoundError, TenantAccessError
from taroai.triggers.models import (
    TriggerDefinition,
    TriggerDefinitionCreate,
    TriggerRunRequest,
    TriggerStatus,
    TriggerType,
)


class TriggerDisabledError(RuntimeError):
    pass


class TriggerStore(BaseModel):
    def create(self, payload: TriggerDefinitionCreate) -> TriggerDefinition:
        raise NotImplementedError

    def get(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        raise NotImplementedError

    def list_by_tenant(self, tenant_id: str) -> list[TriggerDefinition]:
        raise NotImplementedError

    def list_all(self) -> list[TriggerDefinition]:
        raise NotImplementedError

    def update_status(
        self,
        tenant_id: str,
        trigger_id: str,
        status: TriggerStatus,
    ) -> TriggerDefinition:
        raise NotImplementedError

    def update_next_run_at(
        self,
        tenant_id: str,
        trigger_id: str,
        next_run_at: datetime | None,
    ) -> TriggerDefinition:
        raise NotImplementedError

    def delete(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        raise NotImplementedError


class InMemoryTriggerStore(TriggerStore):
    triggers: dict[str, TriggerDefinition] = Field(default_factory=dict)

    def create(self, payload: TriggerDefinitionCreate) -> TriggerDefinition:
        trigger = TriggerDefinition(**payload.model_dump())
        self.triggers[trigger.id] = trigger
        return trigger

    def get(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        trigger = self.triggers.get(trigger_id)
        if trigger is None:
            raise NotFoundError(f"Trigger not found: {trigger_id}")
        if trigger.tenant_id != tenant_id:
            raise TenantAccessError(f"Trigger {trigger_id} is not in tenant {tenant_id}")
        return trigger

    def list_by_tenant(self, tenant_id: str) -> list[TriggerDefinition]:
        return [
            trigger
            for trigger in self.triggers.values()
            if trigger.tenant_id == tenant_id
        ]

    def list_all(self) -> list[TriggerDefinition]:
        return list(self.triggers.values())

    def update_status(
        self,
        tenant_id: str,
        trigger_id: str,
        status: TriggerStatus,
    ) -> TriggerDefinition:
        trigger = self.get(tenant_id, trigger_id)
        updated = trigger.model_copy(update={"status": status})
        self.triggers[trigger_id] = updated
        return updated

    def update_next_run_at(
        self,
        tenant_id: str,
        trigger_id: str,
        next_run_at: datetime | None,
    ) -> TriggerDefinition:
        trigger = self.get(tenant_id, trigger_id)
        updated = trigger.model_copy(
            update={
                "next_run_at": next_run_at,
                "updated_at": utc_now(),
            }
        )
        self.triggers[trigger_id] = updated
        return updated

    def delete(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        trigger = self.get(tenant_id, trigger_id)
        del self.triggers[trigger_id]
        return trigger


class TriggerService(BaseModel):
    store: TriggerStore = Field(default_factory=InMemoryTriggerStore)

    def create_trigger(self, payload: TriggerDefinitionCreate) -> TriggerDefinition:
        return self.store.create(payload)

    def list_triggers(self, tenant_id: str) -> list[TriggerDefinition]:
        return self.store.list_by_tenant(tenant_id)

    def list_schedule_triggers(self) -> list[TriggerDefinition]:
        return [
            trigger
            for trigger in self.store.list_all()
            if trigger.type == TriggerType.SCHEDULE
        ]

    def get_trigger(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        return self.store.get(tenant_id, trigger_id)

    def enable_trigger(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        return self.store.update_status(tenant_id, trigger_id, TriggerStatus.ENABLED)

    def disable_trigger(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        return self.store.update_status(tenant_id, trigger_id, TriggerStatus.DISABLED)

    def delete_trigger(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        trigger = self.store.get(tenant_id, trigger_id)
        if trigger.status != TriggerStatus.DISABLED:
            raise ValueError("Disable the trigger before deleting it")
        return self.store.delete(tenant_id, trigger_id)

    def update_next_run_at(
        self,
        tenant_id: str,
        trigger_id: str,
        next_run_at: datetime | None,
    ) -> TriggerDefinition:
        return self.store.update_next_run_at(tenant_id, trigger_id, next_run_at)

    def build_run_request(
        self,
        tenant_id: str,
        trigger_id: str,
        invoked_by_user_id: str | None = None,
        invocation_payload: dict[str, Any] | None = None,
    ) -> TriggerRunRequest:
        trigger = self.store.get(tenant_id, trigger_id)
        if trigger.status == TriggerStatus.DISABLED:
            raise TriggerDisabledError(f"Trigger is disabled: {trigger.id}")

        requested_by_user_id = (
            invoked_by_user_id
            or trigger.created_by_user_id
            or trigger.service_account_id
        )
        if requested_by_user_id is None:
            raise TenantAccessError(f"Trigger {trigger.id} has no accountable identity")

        payload = invocation_payload or {}
        agent_id = trigger.agent_id
        if trigger.type == TriggerType.AGENT_HANDOFF and trigger.agent_handoff is not None:
            agent_id = trigger.agent_handoff.target_agent_id
        return TriggerRunRequest(
            tenant_id=trigger.tenant_id,
            workspace_id=trigger.workspace_id,
            trigger_id=trigger.id,
            trigger_type=trigger.type,
            requested_by_user_id=requested_by_user_id,
            agent_id=agent_id,
            message=self._message_from_template(trigger),
            invocation_payload_keys=sorted(payload.keys()),
        )

    def _message_from_template(self, trigger: TriggerDefinition) -> str:
        raw_message = trigger.input_template.get("message")
        if isinstance(raw_message, str) and raw_message.strip():
            return raw_message.strip()
        return f"Run trigger: {trigger.name}"
