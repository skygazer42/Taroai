from datetime import datetime

from pydantic import BaseModel, Field

from taroai.lifecycle.models import (
    DataCategory,
    LegalHold,
    LegalHoldCreate,
    LegalHoldScopeType,
    LifecyclePolicy,
    LifecyclePolicyCreate,
)
from taroai.store import NotFoundError


class InMemoryLifecyclePolicyStore(BaseModel):
    policies: list[LifecyclePolicy] = Field(default_factory=list)
    legal_holds: list[LegalHold] = Field(default_factory=list)

    def upsert_policy(self, request: LifecyclePolicyCreate) -> LifecyclePolicy:
        existing = self._find_policy(
            request.tenant_id,
            request.category,
            request.workspace_id,
        )
        if existing is None:
            policy = LifecyclePolicy(**request.model_dump())
        else:
            policy = existing.model_copy(
                update={
                    **request.model_dump(),
                    "id": existing.id,
                    "created_at": existing.created_at,
                }
            )
        self.policies = [
            policy
            if existing_policy.tenant_id == policy.tenant_id
            and existing_policy.category == policy.category
            and existing_policy.workspace_id == policy.workspace_id
            else existing_policy
            for existing_policy in self.policies
        ]
        if existing is None:
            self.policies.append(policy)
        return policy.model_copy(deep=True)

    def get_policy(
        self,
        tenant_id: str,
        category: DataCategory,
        workspace_id: str | None = None,
    ) -> LifecyclePolicy:
        policy = self._find_policy(tenant_id, category, workspace_id)
        if policy is None:
            raise NotFoundError(f"Lifecycle policy not found: {tenant_id}/{category.value}")
        return policy.model_copy(deep=True)

    def resolve_policy(
        self,
        tenant_id: str,
        category: DataCategory,
        workspace_id: str | None = None,
    ) -> LifecyclePolicy:
        if workspace_id is not None:
            workspace_policy = self._find_policy(tenant_id, category, workspace_id)
            if workspace_policy is not None:
                return workspace_policy.model_copy(deep=True)
        return self.get_policy(tenant_id, category)

    def create_legal_hold(self, request: LegalHoldCreate) -> LegalHold:
        hold = LegalHold(**request.model_dump())
        self.legal_holds.append(hold)
        return hold.model_copy(deep=True)

    def release_legal_hold(
        self,
        tenant_id: str,
        legal_hold_id: str,
        released_at: datetime,
    ) -> LegalHold:
        for hold in self.legal_holds:
            if hold.id != legal_hold_id:
                continue
            if hold.tenant_id != tenant_id:
                raise NotFoundError(f"Legal hold not found: {legal_hold_id}")
            released = hold.model_copy(update={"released_at": released_at})
            self.legal_holds = [
                released if existing.id == legal_hold_id else existing
                for existing in self.legal_holds
            ]
            return released.model_copy(deep=True)
        raise NotFoundError(f"Legal hold not found: {legal_hold_id}")

    def list_active_legal_holds(
        self,
        tenant_id: str,
        category: DataCategory,
        scope_type: LegalHoldScopeType,
        scope_id: str,
        now: datetime,
    ) -> list[LegalHold]:
        return [
            hold.model_copy(deep=True)
            for hold in self.legal_holds
            if hold.tenant_id == tenant_id
            and hold.category == category
            and hold.scope_type == scope_type
            and hold.scope_id == scope_id
            and hold.is_active(now)
        ]

    def is_under_legal_hold(
        self,
        tenant_id: str,
        category: DataCategory,
        scope_type: LegalHoldScopeType,
        scope_id: str,
        now: datetime,
    ) -> bool:
        return bool(
            self.list_active_legal_holds(
                tenant_id=tenant_id,
                category=category,
                scope_type=scope_type,
                scope_id=scope_id,
                now=now,
            )
        )

    def _find_policy(
        self,
        tenant_id: str,
        category: DataCategory,
        workspace_id: str | None = None,
    ) -> LifecyclePolicy | None:
        for policy in self.policies:
            if (
                policy.tenant_id == tenant_id
                and policy.category == category
                and policy.workspace_id == workspace_id
            ):
                return policy
        return None
