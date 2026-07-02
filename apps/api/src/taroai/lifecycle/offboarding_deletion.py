from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import utc_now
from taroai.knowledge.models import KnowledgeTenantDeletionResult
from taroai.lifecycle.models import DataCategory, LegalHold, LegalHoldScopeType
from taroai.lifecycle.offboarding import (
    TenantOffboardingPlan,
    TenantOffboardingState,
    TenantOffboardingTransitionError,
)


class TenantOffboardingDeletionRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    deleted_by_user_id: str = Field(min_length=1)
    now: datetime = Field(default_factory=utc_now)


class TenantOffboardingDeletionResult(BaseModel):
    plan: TenantOffboardingPlan
    deleted_count: int = 0
    deleted_storage_object_ids: list[str] = Field(default_factory=list)
    skipped_count: int = 0
    skipped_storage_object_ids: list[str] = Field(default_factory=list)
    legal_hold_count: int = 0
    legal_hold_ids: list[str] = Field(default_factory=list)
    preserved_storage_object_ids: list[str] = Field(default_factory=list)
    deleted_memory_record_count: int = 0
    deleted_memory_record_ids: list[str] = Field(default_factory=list)
    deleted_short_term_memory_count: int = 0
    deleted_knowledge_base_count: int = 0
    deleted_knowledge_base_ids: list[str] = Field(default_factory=list)
    deleted_knowledge_document_count: int = 0
    deleted_knowledge_document_ids: list[str] = Field(default_factory=list)
    deleted_knowledge_chunk_count: int = 0


class TenantOffboardingDeletionService(BaseModel):
    lifecycle_policy_store: Any
    offboarding_store: Any
    storage_catalog: Any
    object_storage: Any
    long_term_memory_service: Any | None = None
    short_term_memory_service: Any | None = None
    knowledge_service: Any | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def execute(
        self,
        request: TenantOffboardingDeletionRequest,
    ) -> TenantOffboardingDeletionResult:
        plan = self.offboarding_store.get_plan(request.tenant_id, request.plan_id)
        if plan.state != TenantOffboardingState.DELETION_PENDING:
            raise TenantOffboardingTransitionError(
                f"Tenant offboarding deletion cannot be started from state {plan.state.value}"
            )

        candidates = self._storage_deletion_candidates(plan)
        held_objects = self._held_storage_objects(candidates, request.now)
        category_holds = self._active_category_holds(plan, request.now)
        if held_objects or category_holds:
            held_ids = list(held_objects)
            hold_ids = self._dedupe_hold_ids(
                [
                    hold
                    for holds in held_objects.values()
                    for hold in holds
                ]
                + category_holds
            )
            blocked = self.offboarding_store.update_plan(
                plan.model_copy(
                    update={
                        "state": TenantOffboardingState.BLOCKED,
                        "blocked_reason": "active_legal_hold",
                        "blocking_legal_hold_ids": hold_ids,
                        "updated_at": request.now,
                    }
                )
            )
            return TenantOffboardingDeletionResult(
                plan=blocked,
                skipped_count=len(held_ids),
                skipped_storage_object_ids=held_ids,
                legal_hold_count=len(hold_ids),
                legal_hold_ids=hold_ids,
                preserved_storage_object_ids=self._preserved_storage_object_ids(plan),
            )

        deleted_memory_ids = self._delete_long_term_memory(plan)
        deleted_short_term_memory_count = self._delete_short_term_memory(plan)
        deleted_knowledge = self._delete_knowledge(plan)
        deleted_ids: list[str] = []
        for storage_object in candidates:
            self.object_storage.delete(storage_object)
            deleted = self.storage_catalog.mark_deleted(
                tenant_id=request.tenant_id,
                storage_object_id=storage_object.id,
                deleted_at=request.now,
            )
            deleted_ids.append(deleted.id)

        deleted_plan = self.offboarding_store.update_plan(
            plan.model_copy(
                update={
                    "state": TenantOffboardingState.DELETED,
                    "deleted_by_user_id": request.deleted_by_user_id,
                    "deleted_at": request.now,
                    "updated_at": request.now,
                }
            )
        )
        return TenantOffboardingDeletionResult(
            plan=deleted_plan,
            deleted_count=len(deleted_ids),
            deleted_storage_object_ids=deleted_ids,
            preserved_storage_object_ids=self._preserved_storage_object_ids(plan),
            deleted_memory_record_count=len(deleted_memory_ids),
            deleted_memory_record_ids=deleted_memory_ids,
            deleted_short_term_memory_count=deleted_short_term_memory_count,
            deleted_knowledge_base_count=len(deleted_knowledge.deleted_base_ids),
            deleted_knowledge_base_ids=deleted_knowledge.deleted_base_ids,
            deleted_knowledge_document_count=len(deleted_knowledge.deleted_document_ids),
            deleted_knowledge_document_ids=deleted_knowledge.deleted_document_ids,
            deleted_knowledge_chunk_count=deleted_knowledge.deleted_chunk_count,
        )

    def _delete_long_term_memory(self, plan: TenantOffboardingPlan) -> list[str]:
        if DataCategory.MEMORY not in plan.categories:
            return []
        if self.long_term_memory_service is None:
            return []
        return self.long_term_memory_service.delete_for_tenant(plan.tenant_id)

    def _delete_short_term_memory(self, plan: TenantOffboardingPlan) -> int:
        if DataCategory.MEMORY not in plan.categories:
            return 0
        if self.short_term_memory_service is None:
            return 0
        return self.short_term_memory_service.delete_for_tenant(plan.tenant_id)

    def _delete_knowledge(self, plan: TenantOffboardingPlan) -> KnowledgeTenantDeletionResult:
        if DataCategory.KNOWLEDGE not in plan.categories:
            return KnowledgeTenantDeletionResult()
        if self.knowledge_service is None:
            return KnowledgeTenantDeletionResult()
        return self.knowledge_service.delete_for_tenant(plan.tenant_id)

    def _storage_deletion_candidates(self, plan: TenantOffboardingPlan) -> list[Any]:
        if DataCategory.STORAGE_OBJECT not in plan.categories:
            return []
        preserved_ids = set(self._preserved_storage_object_ids(plan))
        return [
            storage_object
            for storage_object in self.storage_catalog.list_active(tenant_id=plan.tenant_id)
            if storage_object.id not in preserved_ids
        ]

    def _preserved_storage_object_ids(self, plan: TenantOffboardingPlan) -> list[str]:
        if plan.export_storage_object_id is None:
            return []
        return [plan.export_storage_object_id]

    def _held_storage_objects(
        self,
        storage_objects: list[Any],
        now: datetime,
    ) -> dict[str, list[LegalHold]]:
        held_objects: dict[str, list[LegalHold]] = {}
        for storage_object in storage_objects:
            holds = self._active_legal_holds_for_storage_object(storage_object, now)
            if holds:
                held_objects[storage_object.id] = holds
        return held_objects

    def _active_category_holds(
        self,
        plan: TenantOffboardingPlan,
        now: datetime,
    ) -> list[LegalHold]:
        holds: list[LegalHold] = []
        for category in plan.categories:
            holds.extend(
                self.lifecycle_policy_store.list_active_legal_holds(
                    tenant_id=plan.tenant_id,
                    category=category,
                    scope_type=LegalHoldScopeType.TENANT,
                    scope_id=plan.tenant_id,
                    now=now,
                )
            )
        return holds

    def _active_legal_holds_for_storage_object(
        self,
        storage_object,
        now: datetime,
    ) -> list[LegalHold]:
        scopes = [
            (LegalHoldScopeType.STORAGE_OBJECT, storage_object.id),
            (LegalHoldScopeType.TENANT, storage_object.tenant_id),
        ]
        if storage_object.workspace_id is not None:
            scopes.append((LegalHoldScopeType.WORKSPACE, storage_object.workspace_id))
        if storage_object.run_id is not None:
            scopes.append((LegalHoldScopeType.RUN, storage_object.run_id))
        holds: list[LegalHold] = []
        for scope_type, scope_id in scopes:
            holds.extend(
                self.lifecycle_policy_store.list_active_legal_holds(
                    tenant_id=storage_object.tenant_id,
                    category=DataCategory.STORAGE_OBJECT,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    now=now,
                )
            )
        return holds

    def _dedupe_hold_ids(self, holds) -> list[str]:
        deduped: dict[str, None] = {}
        for hold in holds:
            deduped[hold.id] = None
        return list(deduped)
