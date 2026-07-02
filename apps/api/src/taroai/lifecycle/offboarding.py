from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import new_id, utc_now
from taroai.lifecycle.models import DataCategory, LegalHold, LegalHoldScopeType
from taroai.store import NotFoundError


class TenantOffboardingState(str, Enum):
    REQUESTED = "requested"
    EXPORT_PENDING = "export_pending"
    EXPORT_COMPLETED = "export_completed"
    DELETION_PENDING = "deletion_pending"
    DELETED = "deleted"
    BLOCKED = "blocked"


class TenantOffboardingApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    NOT_REQUIRED = "not_required"


class TenantOffboardingApiRequest(BaseModel):
    reason: str = Field(min_length=1)
    export_before_delete: bool = True
    categories: list[DataCategory] = Field(default_factory=lambda: list(DataCategory))


class TenantOffboardingRequest(TenantOffboardingApiRequest):
    tenant_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)


class TenantOffboardingApprovalRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    approved_by_user_id: str = Field(min_length=1)


class TenantOffboardingExportCompletionRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    completed_by_user_id: str = Field(min_length=1)
    export_bundle_id: str = Field(min_length=1)
    export_storage_object_id: str = Field(min_length=1)


class TenantOffboardingPlan(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tenant_offboarding"))
    tenant_id: str
    requested_by_user_id: str
    state: TenantOffboardingState
    approval_required: bool
    approval_status: TenantOffboardingApprovalStatus
    next_state_after_approval: TenantOffboardingState | None = None
    export_before_delete: bool
    categories: list[DataCategory]
    reason_length: int
    blocked_reason: str | None = None
    blocking_legal_hold_ids: list[str] = Field(default_factory=list)
    deletion_scope: list[str] = Field(default_factory=list)
    approved_by_user_id: str | None = None
    approved_at: datetime | None = None
    export_bundle_id: str | None = None
    export_storage_object_id: str | None = None
    export_completed_by_user_id: str | None = None
    export_completed_at: datetime | None = None
    deleted_by_user_id: str | None = None
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TenantOffboardingTransitionError(RuntimeError):
    pass


class InMemoryTenantOffboardingStore(BaseModel):
    plans: dict[str, TenantOffboardingPlan] = Field(default_factory=dict)

    def save_plan(self, plan: TenantOffboardingPlan) -> TenantOffboardingPlan:
        self.plans[plan.id] = plan.model_copy(deep=True)
        return plan.model_copy(deep=True)

    def get_plan(self, tenant_id: str, plan_id: str) -> TenantOffboardingPlan:
        plan = self.plans.get(plan_id)
        if plan is None or plan.tenant_id != tenant_id:
            raise NotFoundError(f"Tenant offboarding plan not found: {plan_id}")
        return plan.model_copy(deep=True)

    def update_plan(self, plan: TenantOffboardingPlan) -> TenantOffboardingPlan:
        self.get_plan(plan.tenant_id, plan.id)
        self.plans[plan.id] = plan.model_copy(deep=True)
        return plan.model_copy(deep=True)


class TenantOffboardingService(BaseModel):
    lifecycle_policy_store: Any
    offboarding_store: Any = Field(default_factory=InMemoryTenantOffboardingStore)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def create_plan(self, request: TenantOffboardingRequest) -> TenantOffboardingPlan:
        blocking_holds = self._tenant_legal_holds(request)
        if blocking_holds:
            return self.offboarding_store.save_plan(
                TenantOffboardingPlan(
                    tenant_id=request.tenant_id,
                    requested_by_user_id=request.requested_by_user_id,
                    state=TenantOffboardingState.BLOCKED,
                    approval_required=False,
                    approval_status=TenantOffboardingApprovalStatus.NOT_REQUIRED,
                    export_before_delete=request.export_before_delete,
                    categories=request.categories,
                    reason_length=len(request.reason),
                    blocked_reason="active_legal_hold",
                    blocking_legal_hold_ids=[hold.id for hold in blocking_holds],
                    deletion_scope=self._deletion_scope(request.categories),
                )
            )

        return self.offboarding_store.save_plan(
            TenantOffboardingPlan(
                tenant_id=request.tenant_id,
                requested_by_user_id=request.requested_by_user_id,
                state=TenantOffboardingState.REQUESTED,
                approval_required=True,
                approval_status=TenantOffboardingApprovalStatus.PENDING,
                next_state_after_approval=(
                    TenantOffboardingState.EXPORT_PENDING
                    if request.export_before_delete
                    else TenantOffboardingState.DELETION_PENDING
                ),
                export_before_delete=request.export_before_delete,
                categories=request.categories,
                reason_length=len(request.reason),
                deletion_scope=self._deletion_scope(request.categories),
            )
        )

    def get_plan(self, tenant_id: str, plan_id: str) -> TenantOffboardingPlan:
        return self.offboarding_store.get_plan(tenant_id, plan_id)

    def approve_plan(
        self,
        request: TenantOffboardingApprovalRequest,
    ) -> TenantOffboardingPlan:
        plan = self.offboarding_store.get_plan(request.tenant_id, request.plan_id)
        if (
            plan.state != TenantOffboardingState.REQUESTED
            or plan.approval_status != TenantOffboardingApprovalStatus.PENDING
            or plan.next_state_after_approval is None
        ):
            raise TenantOffboardingTransitionError(
                f"Tenant offboarding plan cannot be approved from state {plan.state.value}"
            )
        now = utc_now()
        approved = plan.model_copy(
            update={
                "state": plan.next_state_after_approval,
                "approval_required": False,
                "approval_status": TenantOffboardingApprovalStatus.APPROVED,
                "approved_by_user_id": request.approved_by_user_id,
                "approved_at": now,
                "updated_at": now,
            }
        )
        return self.offboarding_store.update_plan(approved)

    def complete_export(
        self,
        request: TenantOffboardingExportCompletionRequest,
    ) -> TenantOffboardingPlan:
        plan = self.offboarding_store.get_plan(request.tenant_id, request.plan_id)
        if plan.state != TenantOffboardingState.EXPORT_PENDING:
            raise TenantOffboardingTransitionError(
                f"Tenant offboarding export cannot be completed from state {plan.state.value}"
            )
        now = utc_now()
        completed = plan.model_copy(
            update={
                "state": TenantOffboardingState.DELETION_PENDING,
                "export_bundle_id": request.export_bundle_id,
                "export_storage_object_id": request.export_storage_object_id,
                "export_completed_by_user_id": request.completed_by_user_id,
                "export_completed_at": now,
                "updated_at": now,
            }
        )
        return self.offboarding_store.update_plan(completed)

    def _tenant_legal_holds(
        self,
        request: TenantOffboardingRequest,
    ) -> list[LegalHold]:
        now = utc_now()
        holds: list[LegalHold] = []
        for category in request.categories:
            holds.extend(
                self.lifecycle_policy_store.list_active_legal_holds(
                    tenant_id=request.tenant_id,
                    category=category,
                    scope_type=LegalHoldScopeType.TENANT,
                    scope_id=request.tenant_id,
                    now=now,
                )
            )
        return self._dedupe_holds(holds)

    def _dedupe_holds(self, holds: list[LegalHold]) -> list[LegalHold]:
        deduped: dict[str, LegalHold] = {}
        for hold in holds:
            deduped[hold.id] = hold
        return list(deduped.values())

    def _deletion_scope(self, categories: list[DataCategory]) -> list[str]:
        return [category.value for category in categories]
